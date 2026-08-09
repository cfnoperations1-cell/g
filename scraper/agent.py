"""Peptide research-company lead-discovery agent.

Pipeline: run configured search/directory queries -> collect candidate
company URLs -> visit each site's own public pages -> extract contact info
and check for "research use only" language -> upsert qualifying companies
into the CRM database as new leads.

Usage (run from the repo root):
    python -m scraper.agent --dry-run
    python -m scraper.agent --per-query 15 --limit 50
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import config
from db import SessionLocal, init_db
from models import Lead
from scraper.directory_providers import GooglePlacesProvider
from scraper.search_providers import BingSearchProvider, GoogleCustomSearchProvider
from scraper.site_parser import SiteData, get_domain, parse_site

logger = logging.getLogger(__name__)

DEFAULT_QUERIES_FILE = Path(__file__).parent / "queries.txt"


def load_queries(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def candidate_urls_from_search(queries: List[str], per_query: int) -> Iterator[Tuple[str, str, str]]:
    providers = [GoogleCustomSearchProvider(), BingSearchProvider()]
    for provider in providers:
        if not provider.is_configured():
            logger.info("Skipping %s (not configured)", provider.name)
            continue
        for query in queries:
            logger.info("[%s] searching: %s", provider.name, query)
            for result in provider.search(query, per_query):
                yield result.url, query, provider.name


def candidate_urls_from_directory(queries: List[str], per_query: int) -> Iterator[Tuple[str, str, str]]:
    provider = GooglePlacesProvider()
    if not provider.is_configured():
        logger.info("Skipping %s (not configured)", provider.name)
        return
    for query in queries:
        logger.info("[%s] searching: %s", provider.name, query)
        for result in provider.search(query, per_query):
            yield result.website, query, provider.name


def upsert_lead(session, site_data: SiteData, source: str, matched_query: str, require_research_only: bool) -> str:
    """Insert a new lead if it qualifies. Returns a short status string."""
    if require_research_only and not site_data.research_only_evidence:
        return "skipped_not_research_only"

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
        status="new",
    )
    session.add(lead)
    session.commit()
    return "added"


def run(
    queries_file: Path,
    per_query: int,
    limit: Optional[int],
    require_research_only: bool,
    dry_run: bool,
) -> dict:
    init_db()
    queries = load_queries(queries_file)
    if not queries:
        logger.error("No queries loaded from %s", queries_file)
        return {}

    candidates = list(candidate_urls_from_search(queries, per_query))
    candidates += list(candidate_urls_from_directory(queries, per_query))

    seen_domains: set = set()
    stats = {"added": 0, "duplicate": 0, "skipped_not_research_only": 0, "fetch_failed": 0}
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
            site_data = parse_site(url)
            if not site_data.pages_checked:
                stats["fetch_failed"] += 1
                continue

            if dry_run:
                logger.info(
                    "[dry-run] %s | email=%s | research_only=%s",
                    site_data.domain,
                    site_data.email,
                    bool(site_data.research_only_evidence),
                )
                continue

            result = upsert_lead(session, site_data, source, query, require_research_only)
            stats[result] = stats.get(result, 0) + 1
            time.sleep(0.5)  # be polite to target servers
    finally:
        session.close()

    logger.info("Done. Stats: %s", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queries-file", type=Path, default=DEFAULT_QUERIES_FILE)
    parser.add_argument("--per-query", type=int, default=10, help="Results to fetch per query per provider")
    parser.add_argument("--limit", type=int, default=None, help="Max number of new sites to visit")
    parser.add_argument(
        "--allow-non-research",
        action="store_true",
        help="Also keep leads whose site has no 'research use only' language",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing to the CRM database")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    run(
        queries_file=args.queries_file,
        per_query=args.per_query,
        limit=args.limit,
        require_research_only=not args.allow_non_research,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
