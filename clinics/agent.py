"""Clinic lead-discovery agent: US medical clinics that offer peptides.

Where the vendor scraper hunts a national market of companies that *sell*
peptides, this one hunts local practices that *administer* them -- med spas,
hormone/TRT clinics, medical weight-loss clinics, regenerative and
functional-medicine practices, and their telehealth equivalents.

Built around a daily quota. `--daily-target` (default 400) is the number of
NEW clinic leads a run aims to leave in the CRM by the end of the day, and
the agent keeps searching until it gets there or runs out of budget:

  * Query space is every (template x service x city) combination -- roughly
    450 cities x 28 services x 12 templates, so hundreds of thousands of
    distinct searches. A saved cursor (models.DiscoveryState) means each run
    starts where the last one stopped instead of re-running yesterday's
    searches and rediscovering yesterday's clinics.
  * The target counts leads added *today*, not leads added this run, so a
    re-run after a crash tops the day up to 400 rather than adding another
    400.
  * Sites are visited concurrently (--workers), because 400 new leads means
    visiting a few thousand candidate sites, which is not something a
    sequential crawl finishes inside a day.

Usage (from the repo root):
    python -m clinics.agent --dry-run -v --max-queries 5
    python -m clinics.agent --daily-target 400
    python -m clinics.agent --daily-target 400 --state TX --workers 12
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests

from db import SessionLocal, init_db
from models import DiscoveryState, Lead, LeadKind
from clinics.classify import evaluate, is_aggregator_domain
from clinics.query_templates import DIRECTORY_TEMPLATES, SEARCH_TEMPLATES
from scraper.agent import load_keywords
from scraper.directory_providers import GooglePlacesProvider
from scraper.search_providers import (
    BingSearchProvider,
    BraveSearchProvider,
    GoogleCustomSearchProvider,
    SerperProvider,
)
from scraper.site_parser import SiteData, get_domain, parse_site

logger = logging.getLogger(__name__)

AGENT_NAME = "clinics"

DEFAULT_CITIES_FILE = Path(__file__).parent / "us_cities.txt"
DEFAULT_SERVICES_FILE = Path(__file__).parent / "clinic_services.txt"
DEFAULT_PEPTIDES_FILE = Path(__file__).parent.parent / "scraper" / "peptide_keywords.txt"

DEFAULT_DAILY_TARGET = 400

# Clinics describe what they offer on a services/treatments page, not on the
# home page a vendor uses as a storefront -- so the page sweep is different.
CLINIC_PATHS = [
    "", "/services", "/treatments", "/peptide-therapy", "/peptides",
    "/about", "/contact",
]


class City(tuple):
    """(name, state_abbrev) -- a tuple subclass so it stays cheap to sort/hash."""

    @property
    def name(self) -> str:
        return self[0]

    @property
    def state(self) -> str:
        return self[1]


def load_seed_urls(path: Path) -> List[str]:
    """Explicit clinic URLs to visit, bypassing search discovery entirely.

    Every URL still goes through the same fetch -> classify -> save path as a
    searched one, so a seed that turns out not to be a peptide clinic (or not
    to be US-based) is rejected exactly like any other candidate. A seed list
    is a list of *candidates*, never a list of leads.
    """
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def load_cities(path: Path) -> List[City]:
    """Read "City<TAB>ST" rows, ignoring comments and duplicates."""
    if not path.exists():
        return []
    cities: List[City] = []
    seen = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t") if "\t" in line else line.rsplit(",", 1)
        if len(parts) != 2:
            logger.warning("skipping malformed city row: %r", line)
            continue
        city = City((parts[0].strip(), parts[1].strip().upper()))
        if city in seen:
            continue
        seen.add(city)
        cities.append(city)
    return cities


def build_query_space(
    cities: Sequence[City],
    services: Sequence[str],
    templates: Sequence[str] = tuple(SEARCH_TEMPLATES),
) -> List[Tuple[str, str]]:
    """Every (query, city_name) pair, ordered city-major.

    City-major matters twice over: it spreads a day's searches across the
    whole country instead of exhausting one metro before moving on, and it
    means a run that only gets through part of the space still sampled
    everywhere. The ordering is deterministic, which is what lets a saved
    integer cursor stand in for "where we left off".
    """
    space: List[Tuple[str, str]] = []
    for city in cities:
        for template in templates:
            for service in services:
                query = template.format(service=service, city=city.name, state=city.state)
                space.append((query, city.name))
                if "{service}" not in template:
                    break  # city-only template: one query per city, not one per service
    return space


def read_cursor(session, agent: str = AGENT_NAME) -> int:
    state = session.get(DiscoveryState, agent)
    return state.query_cursor if state else 0


def save_cursor(session, cursor: int, queries_run: int, agent: str = AGENT_NAME) -> None:
    state = session.get(DiscoveryState, agent)
    if state is None:
        state = DiscoveryState(agent=agent, query_cursor=0, queries_run=0)
        session.add(state)
    state.query_cursor = cursor
    state.queries_run = (state.queries_run or 0) + queries_run
    state.last_run_at = datetime.utcnow()
    session.commit()


def leads_added_today(session, now: Optional[datetime] = None) -> int:
    """Clinic leads created since midnight UTC.

    Counting from the database rather than from a per-run tally is what makes
    the daily target idempotent: run the agent twice in one day and the second
    run only makes up the shortfall.
    """
    now = now or datetime.utcnow()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.query(Lead)
        .filter(Lead.kind == LeadKind.CLINIC.value, Lead.created_at >= midnight)
        .count()
    )


def configured_search_providers() -> List:
    """The search providers that have an API key, logged once per run rather
    than once per batch (a 400-lead day runs dozens of batches)."""
    providers = [SerperProvider(), BraveSearchProvider(), GoogleCustomSearchProvider(), BingSearchProvider()]
    active = []
    for provider in providers:
        if provider.is_configured():
            active.append(provider)
        else:
            logger.info("Skipping %s (not configured)", provider.name)
    return active


def search_candidates(
    queries: Sequence[Tuple[str, str]],
    per_query: int,
    providers: Sequence,
) -> Iterator[Tuple[str, str, str, str]]:
    """Yield (url, query, source, city) from every configured provider."""
    for query, city in queries:
        for provider in providers:
            logger.debug("[%s] searching: %s", provider.name, query)
            try:
                for result in provider.search(query, per_query):
                    yield result.url, query, provider.name, city
            except requests.RequestException as exc:
                # One provider being rate-limited or misconfigured must not end
                # the run -- a daily quota depends on the others carrying on.
                logger.warning("[%s] query failed, skipping it: %s", provider.name, exc)


def build_directory_queries(cities: Sequence[City]) -> List[Tuple[str, str]]:
    """Every (Places query, city_name) pair for the given cities."""
    return [
        (template.format(city=city.name, state=city.state, service="peptide therapy"), city.name)
        for city in cities
        for template in DIRECTORY_TEMPLATES
    ]


def directory_candidates(queries: Sequence[Tuple[str, str]], per_query: int) -> Iterator[Tuple[str, str, str, str]]:
    """Google Places lookups. Clinics are physical local businesses, so a map
    directory finds ones that never rank in web search."""
    provider = GooglePlacesProvider()
    if not provider.is_configured():
        logger.info("Skipping %s (not configured)", provider.name)
        return
    for query, city_name in queries:
        logger.debug("[%s] searching: %s", provider.name, query)
        try:
            for result in provider.search(query, per_query):
                if result.website:
                    yield result.website, query, provider.name, city_name
        except requests.RequestException as exc:
            logger.warning("[%s] query failed, skipping it: %s", provider.name, exc)
            return


def paths_for(url: str) -> List[str]:
    """The pages to check for this candidate.

    A search result (or a seed) usually points straight at the page that
    proves the clinic offers peptides -- "/wellness/peptide-therapy/", say.
    The standard sweep would normalise that away and check only the generic
    paths, so the one page carrying the evidence goes first, followed by the
    usual pages for contact details.
    """
    path = urlparse(url).path.rstrip("/")
    if path and path not in CLINIC_PATHS:
        return [path] + CLINIC_PATHS
    return CLINIC_PATHS


def visit(url: str, peptide_keywords: List[str], city: Optional[str]) -> Tuple[SiteData, Optional[str], dict]:
    """Fetch one clinic site and assess it. Runs on a worker thread."""
    site_data = parse_site(url, peptide_keywords=None, candidate_paths=paths_for(url))
    if not site_data.pages_checked:
        return site_data, "fetch_failed", {}
    skip_reason, details = evaluate(site_data.full_text, peptide_keywords, queried_city=city)
    return site_data, skip_reason, details


def qualifies(site_data: SiteData, skip_reason: Optional[str], allow_non_us: bool) -> Optional[str]:
    """None if this clinic should be saved, otherwise the stats key for why not."""
    if skip_reason:
        return skip_reason
    if not allow_non_us and not site_data.us_based:
        return "skipped_non_us"
    return None


def save_clinic(session, site_data: SiteData, details: dict, source: str, query: str) -> str:
    """Insert a clinic lead. Returns "added" or "duplicate"."""
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
        matched_query=query[:255],
        research_only_evidence=details.get("peptide_evidence"),
        kind=LeadKind.CLINIC.value,
        clinic_type=details.get("clinic_type"),
        city=details.get("city"),
        peptides_offered=", ".join(details.get("peptides_offered") or []) or None,
        telehealth=bool(details.get("telehealth")),
        sells_direct=site_data.sells_direct,
        us_based=site_data.us_based,
        state=site_data.state,
        status="new",
    )
    session.add(lead)
    session.commit()
    return "added"


def run(
    daily_target: Optional[int] = DEFAULT_DAILY_TARGET,
    per_query: int = 10,
    max_queries: int = 600,
    query_batch: int = 25,
    workers: int = 8,
    allow_non_us: bool = False,
    dry_run: bool = False,
    cities_file: Path = DEFAULT_CITIES_FILE,
    services_file: Path = DEFAULT_SERVICES_FILE,
    peptides_file: Path = DEFAULT_PEPTIDES_FILE,
    state_filter: Optional[str] = None,
    use_directory: bool = True,
    resume: bool = True,
    seed_urls_file: Optional[Path] = None,
) -> dict:
    init_db()

    stats = {
        "added": 0, "duplicate": 0, "fetch_failed": 0, "skipped_not_a_clinic": 0,
        "skipped_no_peptides": 0, "skipped_research_vendor": 0, "skipped_non_us": 0,
        "skipped_no_page_text": 0, "skipped_aggregator": 0, "queries_run": 0,
        "sites_visited": 0,
    }

    # Seed mode: visit exactly these URLs and stop. No search providers needed,
    # no cursor to advance, and no daily target -- the list is the work.
    if seed_urls_file is not None:
        peptide_keywords = load_keywords(peptides_file)
        seeds = load_seed_urls(seed_urls_file)
        logger.info("Loaded %d seed URLs from %s (skipping search discovery)", len(seeds), seed_urls_file)
        session = SessionLocal()
        try:
            seen_domains = {domain for (domain,) in session.query(Lead.domain).all()}
            _process(
                iter([(url, "manual seed list", "seed_list", None) for url in seeds]),
                session, peptide_keywords, seen_domains, stats,
                remaining=None, workers=workers, allow_non_us=allow_non_us, dry_run=dry_run,
            )
        finally:
            session.close()
        logger.info("Done. Stats: %s", stats)
        return stats

    cities = load_cities(cities_file)
    services = load_keywords(services_file)
    peptide_keywords = load_keywords(peptides_file)
    if not cities or not services:
        logger.error("No cities (%s) or services (%s) loaded", cities_file, services_file)
        return {}
    if state_filter:
        wanted = {s.strip().upper() for s in state_filter.split(",") if s.strip()}
        cities = [city for city in cities if city.state in wanted]
        if not cities:
            logger.error("No cities left after filtering to state(s): %s", sorted(wanted))
            return {}

    query_space = build_query_space(cities, services)
    logger.info(
        "Query space: %d searches (%d cities x %d services x %d templates)",
        len(query_space), len(cities), len(services), len(SEARCH_TEMPLATES),
    )

    search_providers = configured_search_providers()
    places = GooglePlacesProvider()
    if not search_providers and not (use_directory and places.is_configured()):
        logger.error(
            "No search or directory provider is configured -- nothing to discover with. "
            "Add SERPER_API_KEY (or BRAVE_SEARCH_API_KEY / GOOGLE_CSE_* / GOOGLE_PLACES_API_KEY) to .env."
        )
        return {}

    session = SessionLocal()

    try:
        cursor = read_cursor(session) if resume else 0
        already = leads_added_today(session)
        if daily_target is not None:
            remaining = daily_target - already
            logger.info("Daily target %d; %d clinic leads already added today; %d to go",
                        daily_target, already, remaining)
            if remaining <= 0:
                logger.info("Target already met for today -- nothing to do.")
                return stats
        else:
            remaining = None
            logger.info("No daily target set; running until the query budget is spent.")

        seen_domains = {domain for (domain,) in session.query(Lead.domain).all()}
        queries_used = 0

        # Places lookups are cheap per clinic found and turn up practices with
        # no search-engine footprint, so they lead the run when configured.
        if use_directory and places.is_configured():
            # Spend at most a fifth of the query budget here, and rotate which
            # cities get a map lookup using the same cursor as web search, so
            # consecutive days cover different metros.
            directory_budget = max(1, max_queries // 5)
            city_slots = max(1, directory_budget // len(DIRECTORY_TEMPLATES))
            offset = cursor % len(cities)
            rotated = [cities[(offset + i) % len(cities)] for i in range(min(city_slots, len(cities)))]
            directory_queries = build_directory_queries(rotated)
            queries_used += len(directory_queries)
            stats["queries_run"] += len(directory_queries)
            _process(
                directory_candidates(directory_queries, per_query), session, peptide_keywords,
                seen_domains, stats, remaining, workers, allow_non_us, dry_run,
            )
            if remaining is not None:
                remaining = daily_target - leads_added_today(session)

        while search_providers and queries_used < max_queries and (remaining is None or remaining > 0):
            batch_end = cursor + query_batch
            batch = [query_space[i % len(query_space)] for i in range(cursor, batch_end)]
            cursor = batch_end % len(query_space)
            queries_used += len(batch)
            stats["queries_run"] += len(batch)

            candidates = search_candidates(batch, per_query, search_providers)
            _process(
                candidates, session, peptide_keywords, seen_domains, stats,
                remaining, workers, allow_non_us, dry_run,
            )

            if remaining is not None:
                remaining = daily_target - leads_added_today(session)
                logger.info("progress: %d added this run, %d still needed today (cursor %d, %d queries used)",
                            stats["added"], max(0, remaining), cursor, queries_used)

        if not dry_run:
            save_cursor(session, cursor, stats["queries_run"])
    finally:
        session.close()

    logger.info("Done. Stats: %s", stats)
    return stats


def _process(
    candidates: Iterator[Tuple[str, str, str, str]],
    session,
    peptide_keywords: List[str],
    seen_domains: set,
    stats: dict,
    remaining: Optional[int],
    workers: int,
    allow_non_us: bool,
    dry_run: bool,
) -> None:
    """Visit candidate sites concurrently and save the ones that qualify.

    Reports everything through `stats`; query accounting is the caller's.
    Kept separate so the directory and search streams share one
    fetch/classify/save path.
    """
    # Dedupe before dispatching so no two workers fetch the same domain, and
    # so a domain already in the CRM is never fetched at all.
    pending: List[Tuple[str, str, str, str]] = []
    for url, query, source, city in candidates:
        domain = get_domain(url)
        if not domain or domain in seen_domains:
            continue
        if is_aggregator_domain(domain):
            stats["skipped_aggregator"] += 1
            seen_domains.add(domain)
            continue
        seen_domains.add(domain)
        pending.append((url, query, source, city))

    if not pending:
        return

    limit = len(pending) if remaining is None else min(len(pending), max(remaining * 8, 25))
    pending = pending[:limit]
    logger.info("visiting %d candidate clinic sites with %d workers", len(pending), workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(visit, url, peptide_keywords, city): (url, query, source)
            for url, query, source, city in pending
        }
        for future in as_completed(futures):
            url, query, source = futures[future]
            try:
                site_data, skip_reason, details = future.result()
            except Exception as exc:  # one bad site must not end the batch
                logger.debug("visit failed for %s: %s", url, exc)
                stats["fetch_failed"] += 1
                continue

            stats["sites_visited"] += 1
            reason = qualifies(site_data, skip_reason, allow_non_us)

            if dry_run:
                logger.info(
                    "[dry-run] %-32s | %-20s | %s | us=%s (%s) | %s",
                    site_data.domain,
                    details.get("clinic_type") or (reason or "-"),
                    "telehealth" if details.get("telehealth") else "in-person",
                    site_data.us_based, site_data.state or "?",
                    site_data.email or "no email",
                )
                continue

            if reason:
                stats[reason] = stats.get(reason, 0) + 1
                continue

            result = save_clinic(session, site_data, details, source, query)
            stats[result] = stats.get(result, 0) + 1
            if result == "added":
                logger.info("added %s (%s, %s)", site_data.domain,
                            details.get("clinic_type"), site_data.state or "?")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--daily-target", type=int, default=DEFAULT_DAILY_TARGET,
        help=f"New clinic leads to have in the CRM by end of day (default {DEFAULT_DAILY_TARGET}). "
             "Counts leads added today, so re-running only makes up the shortfall. 0 = no target.",
    )
    parser.add_argument("--per-query", type=int, default=10, help="Results per query per provider")
    parser.add_argument(
        "--max-queries", type=int, default=600,
        help="Hard cap on searches this run, so a bad day can't burn the whole API budget (default 600). "
             "Counts logical searches; each one is sent to every configured provider.",
    )
    parser.add_argument("--query-batch", type=int, default=25, help="Searches between target re-checks")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent site fetches (default 8)")
    parser.add_argument("--state", type=str, default=None, help="Limit to these states, e.g. TX,FL,CA")
    parser.add_argument("--cities-file", type=Path, default=DEFAULT_CITIES_FILE)
    parser.add_argument("--services-file", type=Path, default=DEFAULT_SERVICES_FILE)
    parser.add_argument("--peptides-file", type=Path, default=DEFAULT_PEPTIDES_FILE)
    parser.add_argument("--allow-non-us", action="store_true", help="Keep clinics that don't look US-based")
    parser.add_argument("--no-directory", action="store_true", help="Skip Google Places discovery")
    parser.add_argument(
        "--restart-cursor", action="store_true",
        help="Start from the top of the query space instead of resuming where the last run stopped",
    )
    parser.add_argument(
        "--seed-urls-file", type=Path, default=None,
        help="Skip search discovery and visit exactly these clinic URLs instead (one per line, # for "
             "comments). Each is still fetched, classified and filtered like any other candidate.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print what it finds; write nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    run(
        daily_target=args.daily_target or None,
        per_query=args.per_query,
        max_queries=args.max_queries,
        query_batch=args.query_batch,
        workers=args.workers,
        allow_non_us=args.allow_non_us,
        dry_run=args.dry_run,
        cities_file=args.cities_file,
        services_file=args.services_file,
        peptides_file=args.peptides_file,
        state_filter=args.state,
        use_directory=not args.no_directory,
        resume=not args.restart_cursor,
        seed_urls_file=args.seed_urls_file,
    )


if __name__ == "__main__":
    main()
