"""Load a curated vendor roster into the CRM, keeping every company.

This is the counterpart to scraper.agent. The agent evaluates *unknown*
candidates that came out of a web search, so it filters hard -- most of what
a search returns is a blog or an irrelevant shop. A roster you assembled by
hand is the opposite situation: the vetting already happened, so every name
belongs in the CRM even if its site blocks crawlers or reveals nothing.

So nothing here is dropped. Each vendor becomes a lead; visiting the site is
best-effort enrichment that fills in email, phone and classification when it
works, and leaves the lead in place when it doesn't.

Usage:
    python -m scraper.import_vendors --dry-run
    python -m scraper.import_vendors                 # all countries
    python -m scraper.import_vendors --country "United States"
    python -m scraper.import_vendors --no-enrich     # names/domains only, no fetching
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple

from db import SessionLocal, init_db
from models import Lead
from scraper.agent import DEFAULT_KEYWORDS_FILE, load_keywords
from scraper.resolve_vendors import (
    DEFAULT_MAP_FILE,
    DEFAULT_NAMES_FILE,
    first_configured_provider,
    load_domain_map,
    load_vendor_names,
    pick_domain,
    write_domain_map,
)
from scraper.site_parser import parse_site

logger = logging.getLogger(__name__)

US_COUNTRY = "United States"


def resolve_missing(
    vendors: List[Tuple[str, str]], known: dict, threshold: float
) -> Tuple[dict, List[str]]:
    """Search for a website for any roster name we don't have a domain for."""
    missing = [(n, c) for n, c in vendors if n not in known]
    if not missing:
        return {}, []

    provider = first_configured_provider()
    if provider is None:
        logger.warning("No search provider configured; %d vendors have no domain yet", len(missing))
        return {}, [n for n, _ in missing]

    logger.info("Resolving %d vendor names with no known domain via %s", len(missing), provider.name)
    found, still_missing = {}, []

    for name, country in missing:
        try:
            urls = [r.url for r in provider.search(f'"{name}" peptides official site', 6)]
        except Exception as exc:
            logger.warning("  search failed for %r: %s", name, exc)
            still_missing.append(name)
            continue

        host = pick_domain(name, urls, threshold)
        if host:
            logger.info("  %-34s -> %s", name, host)
            found[name] = (country, host)
        else:
            logger.info("  %-34s -> (no confident match)", name)
            still_missing.append(name)
        time.sleep(0.2)

    return found, still_missing


def enrich(lead: Lead, peptide_keywords: List[str]) -> str:
    """Try to fill contact details from the vendor's own site.

    Returns a short status. A site that blocks us or reveals nothing is not a
    failure of the lead -- the company stays, just with less detail.
    """
    try:
        data = parse_site(lead.website, peptide_keywords)
    except Exception as exc:
        logger.debug("enrich failed for %s: %s", lead.domain, exc)
        return "unreachable"

    if not data.pages_checked:
        return "unreachable"

    if data.email and not lead.email:
        lead.email = data.email
    if data.phone and not lead.phone:
        lead.phone = data.phone
    if data.description and not lead.description:
        lead.description = data.description
    if data.company_type and not lead.company_type:
        lead.company_type = data.company_type
    if data.research_only_evidence and not lead.research_only_evidence:
        lead.research_only_evidence = data.research_only_evidence
    if data.state and not lead.state:
        lead.state = data.state
    lead.sells_direct = lead.sells_direct or data.sells_direct
    lead.manufactures = lead.manufactures or data.manufactures

    return "enriched" if data.email else "reached"


def run(
    names_file: Path,
    map_file: Path,
    country: Optional[str],
    do_enrich: bool,
    threshold: float,
    limit: Optional[int],
    dry_run: bool,
) -> dict:
    init_db()
    vendors = load_vendor_names(names_file)
    if country:
        vendors = [(n, c) for n, c in vendors if c.lower() == country.lower()]
    if limit is not None:
        vendors = vendors[:limit]

    known = load_domain_map(map_file)
    newly_found, unresolved = resolve_missing(vendors, known, threshold)
    if newly_found and not dry_run:
        write_domain_map([(n, c, d) for n, (c, d) in newly_found.items()], map_file)
    known.update(newly_found)

    stats = {"added": 0, "updated": 0, "no_domain": 0, "enriched": 0, "unreachable": 0}
    peptide_keywords = load_keywords(DEFAULT_KEYWORDS_FILE) if do_enrich else []
    session = SessionLocal()

    try:
        for name, vendor_country in vendors:
            entry = known.get(name)
            if entry is None:
                # Keep the company visible even with no website found -- you can
                # fill the domain in by hand rather than lose the name entirely.
                logger.info("no domain: %s (%s)", name, vendor_country)
                stats["no_domain"] += 1
                continue

            _, domain = entry
            if dry_run:
                logger.info("[dry-run] %-34s %s", name, domain)
                stats["added"] += 1
                continue

            lead = session.query(Lead).filter_by(domain=domain).one_or_none()
            if lead is None:
                lead = Lead(
                    company_name=name,
                    website=f"https://{domain}/",
                    domain=domain,
                    source="vendor_roster",
                    us_based=(vendor_country == US_COUNTRY),
                    status="new",
                )
                session.add(lead)
                stats["added"] += 1
            else:
                # Already discovered by the scraper -- keep it, note the roster.
                if lead.source and "vendor_roster" not in lead.source:
                    lead.source = f"{lead.source}+vendor_roster"
                stats["updated"] += 1

            if do_enrich and not lead.email:
                result = enrich(lead, peptide_keywords)
                stats["enriched" if result == "enriched" else "unreachable"] += 1

            session.commit()
    finally:
        session.close()

    logger.info("Done. %s", stats)
    if unresolved:
        print(f"\nNo website found for {len(unresolved)}: {', '.join(unresolved[:15])}"
              + (" ..." if len(unresolved) > 15 else ""))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--names-file", type=Path, default=DEFAULT_NAMES_FILE)
    parser.add_argument("--map-file", type=Path, default=DEFAULT_MAP_FILE)
    parser.add_argument("--country", default="", help='Filter by country; default is every country')
    parser.add_argument("--no-enrich", action="store_true", help="Skip visiting sites for contact details")
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    run(
        names_file=args.names_file,
        map_file=args.map_file,
        country=args.country or None,
        do_enrich=not args.no_enrich,
        threshold=args.threshold,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
