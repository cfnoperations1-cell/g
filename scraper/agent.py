"""Peptide-industry lead-discovery agent.

Pipeline: combine peptide keywords with per-company-type query templates ->
run those queries against search/directory APIs -> collect candidate company
URLs -> visit each site's own public pages -> classify by company type
(research-only supplier / consumer+research supplier / compounding pharmacy /
manufacturing lab) and guess US presence -> save qualifying companies into
the CRM database as new leads.

Usage (run from the repo root):
    python -m scraper.agent --dry-run -v
    python -m scraper.agent --max-queries 20 --per-query 10 --limit 50
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import requests

import config
from db import SessionLocal, init_db
from models import Lead, LeadKind
from scraper.directory_providers import GooglePlacesProvider
from scraper.query_templates import QUERY_TEMPLATES
from scraper.search_providers import (
    BingSearchProvider,
    BraveSearchProvider,
    GoogleCustomSearchProvider,
    SerperProvider,
)
from scraper.site_parser import SiteData, get_domain, parse_site

logger = logging.getLogger(__name__)

DEFAULT_KEYWORDS_FILE = Path(__file__).parent / "peptide_keywords.txt"

# Default query focus: B2C peptide brands and general peptide companies.
# Compounding pharmacies and manufacturing labs are still fully supported
# (classify_company_type will still tag one correctly if you land on it),
# just not actively searched for unless you pass --company-types.
DEFAULT_COMPANY_TYPES = ["research_only", "consumer_and_research"]


def load_keywords(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def load_seed_urls(path: Path) -> List[str]:
    """Load explicit candidate URLs, bypassing search/directory discovery
    entirely. Useful for testing the fetch->classify->save pipeline without
    a configured search provider, or for feeding in known company lists."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def build_queries(peptide_keywords: List[str], max_queries: Optional[int], company_types: List[str]) -> List[str]:
    """Build search queries from every (company type, template, keyword)
    combination for the requested company types, interleaved round-robin
    across those types so a small --max-queries budget still samples each
    one evenly."""
    unknown = set(company_types) - set(QUERY_TEMPLATES)
    if unknown:
        raise ValueError(f"Unknown company type(s): {sorted(unknown)}. Valid: {sorted(QUERY_TEMPLATES)}")

    per_type_queries = {
        company_type: [
            template.format(peptide=keyword) for template in QUERY_TEMPLATES[company_type] for keyword in peptide_keywords
        ]
        for company_type in company_types
    }

    interleaved: List[str] = []
    max_len = max((len(queries) for queries in per_type_queries.values()), default=0)
    for i in range(max_len):
        for queries in per_type_queries.values():
            if i < len(queries):
                interleaved.append(queries[i])

    if max_queries is not None:
        interleaved = interleaved[:max_queries]
    return interleaved


def candidate_urls_from_search(queries: List[str], per_query: int) -> Iterator[Tuple[str, str, str]]:
    providers = [GoogleCustomSearchProvider(), SerperProvider(), BraveSearchProvider(), BingSearchProvider()]
    for provider in providers:
        if not provider.is_configured():
            logger.info("Skipping %s (not configured)", provider.name)
            continue
        for query in queries:
            logger.info("[%s] searching: %s", provider.name, query)
            # A provider that is misconfigured on the vendor's side (bad key,
            # API not enabled, quota exhausted) must not abort the whole run --
            # the remaining providers may well be working.
            try:
                for result in provider.search(query, per_query):
                    yield result.url, query, provider.name
            except requests.RequestException as exc:
                logger.warning("[%s] query failed, skipping this provider: %s", provider.name, exc)
                break


def candidate_urls_from_directory(queries: List[str], per_query: int) -> Iterator[Tuple[str, str, str]]:
    provider = GooglePlacesProvider()
    if not provider.is_configured():
        logger.info("Skipping %s (not configured)", provider.name)
        return
    for query in queries:
        logger.info("[%s] searching: %s", provider.name, query)
        try:
            for result in provider.search(query, per_query):
                yield result.website, query, provider.name
        except requests.RequestException as exc:
            logger.warning("[%s] query failed, skipping this provider: %s", provider.name, exc)
            return


def is_qualifying_lead(site_data: SiteData, allow_non_us: bool, vendors_only: bool = True) -> Optional[str]:
    """Return None if the lead qualifies, otherwise a stats key explaining why not."""
    if site_data.company_type is None:
        return "skipped_not_relevant"
    if vendors_only and site_data.is_content_site and not site_data.sells_direct:
        return "skipped_content_site"
    if vendors_only and not site_data.sells_direct:
        return "skipped_not_a_vendor"
    if not allow_non_us and not site_data.us_based:
        return "skipped_non_us"
    return None


def upsert_lead(session, site_data: SiteData, source: str, matched_query: str) -> str:
    """Insert a new lead. Returns a short status string. Assumes the caller
    has already checked is_qualifying_lead()."""
    existing = session.query(Lead).filter_by(domain=site_data.domain).one_or_none()
    if existing:
        return "duplicate"

    lead = Lead(
        company_name=site_data.company_name or site_data.domain,
        website=site_data.url,
        domain=site_data.domain,
        email=site_data.email,
        phone=site_data.phone,
        description=site_data.description,
        source=source,
        matched_query=matched_query,
        research_only_evidence=site_data.research_only_evidence,
        kind=LeadKind.VENDOR.value,
        company_type=site_data.company_type,
        sells_direct=site_data.sells_direct,
        manufactures=site_data.manufactures,
        us_based=site_data.us_based,
        state=site_data.state,
        status="new",
    )
    session.add(lead)
    session.commit()
    return "added"


def run(
    keywords_file: Path,
    per_query: int,
    limit: Optional[int],
    max_queries: Optional[int],
    company_types: List[str],
    allow_non_us: bool,
    dry_run: bool,
    seed_urls_file: Optional[Path] = None,
    vendors_only: bool = True,
) -> dict:
    init_db()
    peptide_keywords = load_keywords(keywords_file)
    if not peptide_keywords:
        logger.error("No peptide keywords loaded from %s", keywords_file)
        return {}

    if seed_urls_file is not None:
        seed_urls = load_seed_urls(seed_urls_file)
        logger.info("Loaded %d seed URLs from %s (skipping search/directory discovery)", len(seed_urls), seed_urls_file)
        candidates = [(url, "manual seed list", "seed_list") for url in seed_urls]
    else:
        queries = build_queries(peptide_keywords, max_queries, company_types)
        logger.info(
            "Built %d queries from %d peptide keywords across company types: %s",
            len(queries), len(peptide_keywords), ", ".join(company_types),
        )
        candidates = list(candidate_urls_from_search(queries, per_query))
        candidates += list(candidate_urls_from_directory(queries, per_query))

    seen_domains: set = set()
    stats = {
        "added": 0, "duplicate": 0, "skipped_not_relevant": 0,
        "skipped_content_site": 0, "skipped_not_a_vendor": 0,
        "skipped_non_us": 0, "fetch_failed": 0,
    }
    processed = 0
    session = SessionLocal()

    try:
        for url, query, source in candidates:
            if limit is not None and processed >= limit:
                break
            domain = get_domain(url)
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            processed += 1

            logger.info("Visiting %s (via %s / %r)", url, source, query)
            site_data = parse_site(url, peptide_keywords)
            if not site_data.pages_checked:
                stats["fetch_failed"] += 1
                continue

            if dry_run:
                logger.info(
                    "[dry-run] %s | type=%s | sells=%s mfg=%s content=%s | us=%s (%s) | %s",
                    site_data.domain,
                    site_data.company_type,
                    site_data.sells_direct,
                    site_data.manufactures,
                    site_data.is_content_site,
                    site_data.us_based,
                    site_data.state,
                    site_data.email,
                )
                continue

            skip_reason = is_qualifying_lead(site_data, allow_non_us, vendors_only)
            if skip_reason:
                stats[skip_reason] = stats.get(skip_reason, 0) + 1
                continue

            result = upsert_lead(session, site_data, source, query)
            stats[result] = stats.get(result, 0) + 1
            time.sleep(0.5)  # be polite to target servers
    finally:
        session.close()

    logger.info("Done. Stats: %s", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keywords-file", type=Path, default=DEFAULT_KEYWORDS_FILE)
    parser.add_argument("--per-query", type=int, default=10, help="Results to fetch per query per provider")
    parser.add_argument("--limit", type=int, default=None, help="Max number of new sites to visit")
    parser.add_argument(
        "--max-queries",
        type=int,
        default=20,
        help="Cap total queries sent to search/directory APIs (mind daily free-tier quotas)",
    )
    parser.add_argument(
        "--allow-non-us",
        action="store_true",
        help="Also keep leads that don't appear to be US-based (by default only US companies are kept)",
    )
    parser.add_argument(
        "--company-types",
        type=str,
        default=",".join(DEFAULT_COMPANY_TYPES),
        help=(
            "Comma-separated company types to actively search for: "
            "research_only, consumer_and_research, compounding_pharmacy, manufacturing_lab "
            f"(default: {','.join(DEFAULT_COMPANY_TYPES)})"
        ),
    )
    parser.add_argument(
        "--seed-urls-file",
        type=Path,
        default=None,
        help="Skip search/directory discovery and visit exactly these URLs instead (one per line, # for comments)",
    )
    parser.add_argument(
        "--include-non-vendors",
        action="store_true",
        help="Also keep sites that don't sell directly (blogs, directories, info pages). "
             "By default only storefronts selling their own product are kept.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing to the CRM database")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    run(
        keywords_file=args.keywords_file,
        per_query=args.per_query,
        limit=args.limit,
        max_queries=args.max_queries,
        company_types=[t.strip() for t in args.company_types.split(",") if t.strip()],
        allow_non_us=args.allow_non_us,
        dry_run=args.dry_run,
        seed_urls_file=args.seed_urls_file,
        vendors_only=not args.include_non_vendors,
    )


if __name__ == "__main__":
    main()
