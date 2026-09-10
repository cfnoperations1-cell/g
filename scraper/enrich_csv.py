"""Fill in the blanks on a vendor spreadsheet.

Takes a CSV of vendors with patchy data (a name, maybe a directory profile
link, sometimes a website, rarely an email) and completes each row from
sources that publish the vendor's own details:

  1. Finnrick's public vendor API  -> website, email, WhatsApp, Telegram
  2. scraper/vendor_domains.tsv    -> website, for names resolved before
  3. a web search (keyed API, or the keyless DuckDuckGo fallback)
                                   -> website, when nothing above had one
  4. the vendor's own site         -> email, phone, Instagram, whether it
                                      actually mentions peptides, company type

Nothing is ever invented: a cell that no source can fill stays empty, and
existing values are never overwritten. Every row records where each fill
came from so you can judge it.

Usage:
    python -m scraper.enrich_csv vendors.csv                       # -> data/out/vendors_enriched.csv
    python -m scraper.enrich_csv vendors.csv --out exports/done.csv
    python -m scraper.enrich_csv vendors.csv --no-visit            # directory sources only, no site crawling
    python -m scraper.enrich_csv vendors.csv --limit 20            # quick test
    python -m scraper.enrich_csv vendors.csv --import-crm          # also load the rows into the CRM
    python -m scraper.enrich_csv vendors.csv --resume              # keep rows already done in --out

The input only needs a vendor-name column ("Vendor", "Name" or "Company");
any of the other columns below are created if missing.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

import config
from scraper.agent import DEFAULT_KEYWORDS_FILE, load_keywords
from scraper.finnrick import FinnrickClient, FinnrickContacts, normalize_name, slug_from_profile_url
from scraper.resolve_vendors import DEFAULT_MAP_FILE, first_configured_provider, load_domain_map, pick_domain
from scraper.site_parser import get_domain, parse_site, shutdown_browser

logger = logging.getLogger(__name__)

NAME_COLUMNS = ("Vendor", "Name", "Company", "Company Name")

FILL_COLUMNS = [
    "Country", "Finnrick Profile", "Website", "Email", "WhatsApp",
    "Telegram/Signal", "Phone", "Social", "Email Source", "Notes",
]

ADDED_COLUMNS = [
    "Website Source", "Site Status", "Peptide Confirmed", "Company Type",
    "Detected US State", "Instagram", "Enrichment", "Enriched At",
]

US_COUNTRY = "United States"


def name_column(fieldnames: List[str]) -> str:
    for candidate in NAME_COLUMNS:
        if candidate in fieldnames:
            return candidate
    raise ValueError(f"No vendor-name column found; expected one of {NAME_COLUMNS}")


def normalize_website(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


def _blank(row: dict, column: str) -> bool:
    return not (row.get(column) or "").strip()


def _set(row: dict, column: str, value: Optional[str], log: List[str], source: str) -> bool:
    """Fill `column` only if it is empty. Returns True when it was filled."""
    if value and _blank(row, column):
        row[column] = value
        log.append(f"{column.lower()}<-{source}")
        return True
    return False


def apply_finnrick(row: dict, contacts: FinnrickContacts, log: List[str]) -> None:
    _set(row, "Website", contacts.website, log, "finnrick")
    if row.get("Website Source") == "" and contacts.website and row.get("Website") == contacts.website:
        row["Website Source"] = "finnrick"
    if contacts.emails:
        # Prefer an address on the vendor's own domain over a webmail one.
        site_domain = get_domain(normalize_website(row.get("Website", ""))) if row.get("Website") else ""
        ranked = sorted(contacts.emails, key=lambda e: 0 if site_domain and e.endswith("@" + site_domain) else 1)
        if _set(row, "Email", ranked[0], log, "finnrick"):
            row["Email Source"] = row.get("Email Source") or "finnrick.com"
    _set(row, "WhatsApp", "; ".join(contacts.whatsapp), log, "finnrick")
    handles = list(contacts.telegram) + [f"Signal {s}" for s in contacts.signal]
    _set(row, "Telegram/Signal", "; ".join(handles), log, "finnrick")
    _set(row, "Social", "; ".join(contacts.other), log, "finnrick")
    _set(row, "Finnrick Profile", f"https://www.finnrick.com/vendors/{contacts.slug}" if contacts.slug else None,
         log, "finnrick")


def enrich_row(
    row: dict,
    name_col: str,
    finnrick: Optional[FinnrickClient],
    domain_map: Dict[str, str],
    resolver,
    visit: bool,
    keywords: List[str],
    threshold: float = 0.55,
) -> dict:
    """Return a completed copy of `row`. Pure apart from the sources passed in."""
    row = dict(row)
    for column in FILL_COLUMNS + ADDED_COLUMNS:
        row.setdefault(column, "")
    log: List[str] = []
    name = (row.get(name_col) or "").strip()

    if row.get("Website"):
        row["Website Source"] = row.get("Website Source") or "original"

    # 1. Finnrick
    if finnrick is not None and name:
        slug = slug_from_profile_url(row.get("Finnrick Profile", ""))
        try:
            contacts = finnrick.lookup(slug=slug, name=name)
        except requests.RequestException as exc:
            contacts = None
            log.append(f"finnrick error: {str(exc)[:80]}")
        if contacts is not None and contacts.found_anything:
            apply_finnrick(row, contacts, log)

    # 2. Previously resolved domains
    if _blank(row, "Website") and name:
        domain = domain_map.get(normalize_name(name))
        if domain and _set(row, "Website", f"https://{domain}/", log, "vendor_domains.tsv"):
            row["Website Source"] = "vendor_domains.tsv"

    # 3. Search
    if _blank(row, "Website") and resolver is not None and name:
        try:
            urls = [r.url for r in resolver.search(f'"{name}" peptides official site', 6)]
        except Exception as exc:
            urls = []
            log.append(f"search error: {str(exc)[:80]}")
        host = pick_domain(name, urls, threshold)
        if host and _set(row, "Website", f"https://{host}/", log, f"search:{resolver.name}"):
            row["Website Source"] = f"search:{resolver.name}"

    # 4. The vendor's own site
    if _blank(row, "Website"):
        row["Site Status"] = "no website"
    elif not visit:
        row["Site Status"] = "not visited"
    else:
        data = parse_site(normalize_website(row["Website"]), keywords)
        if not data.pages_checked:
            row["Site Status"] = "unreachable"
        else:
            row["Site Status"] = "reached (browser)" if data.used_browser else "reached"
            if _set(row, "Email", data.email, log, "site"):
                row["Email Source"] = row.get("Email Source") or data.domain
            _set(row, "Phone", data.phone, log, "site")
            _set(row, "Instagram", data.instagram, log, "site")
            if data.instagram and _blank(row, "Social"):
                row["Social"] = f"Instagram {data.instagram}"
            row["Peptide Confirmed"] = "yes" if data.company_type else "no"
            row["Company Type"] = data.company_type or ""
            row["Detected US State"] = data.state or ""

    row["Enrichment"] = "; ".join(log) if log else "nothing new"
    row["Enriched At"] = time.strftime("%Y-%m-%d %H:%M")
    return row


def load_done_rows(path: Path, name_col: str) -> Dict[str, dict]:
    """Rows from a previous, possibly interrupted, run of this command."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {r[name_col]: r for r in csv.DictReader(fh) if r.get("Enriched At")}


def import_to_crm(rows: List[dict], name_col: str) -> dict:
    """Load enriched rows into the CRM: new leads for new domains, blanks
    filled on leads that already exist. Rows without a website are skipped
    (a lead needs a domain to key on)."""
    from db import SessionLocal, init_db
    from models import Lead

    init_db()
    stats = {"added": 0, "updated": 0, "no_website": 0}
    session = SessionLocal()
    try:
        for row in rows:
            website = normalize_website(row.get("Website", ""))
            domain = get_domain(website) if website else ""
            if not domain:
                stats["no_website"] += 1
                continue
            country = (row.get("Country") or "").strip()
            us_based = country.startswith(US_COUNTRY) or bool(row.get("Detected US State"))
            note_bits = [f"{k}: {row[k]}" for k in ("Listed On", "PeptideBase Tier", "WhatsApp", "Telegram/Signal", "Social")
                         if row.get(k)]
            if row.get("Notes"):
                note_bits.append(row["Notes"])
            lead = session.query(Lead).filter_by(domain=domain).one_or_none()
            if lead is None:
                lead = Lead(
                    company_name=row.get(name_col) or domain,
                    website=website,
                    domain=domain,
                    email=row.get("Email") or None,
                    phone=row.get("Phone") or None,
                    source="vendor_csv",
                    company_type=row.get("Company Type") or None,
                    us_based=us_based,
                    state=row.get("Detected US State") or None,
                    notes="\n".join(note_bits) or None,
                    status="new",
                )
                session.add(lead)
                stats["added"] += 1
            else:
                lead.email = lead.email or row.get("Email") or None
                lead.phone = lead.phone or row.get("Phone") or None
                lead.company_type = lead.company_type or row.get("Company Type") or None
                lead.state = lead.state or row.get("Detected US State") or None
                lead.us_based = lead.us_based or us_based
                if lead.source and "vendor_csv" not in lead.source:
                    lead.source = f"{lead.source}+vendor_csv"
                stats["updated"] += 1
            session.commit()
    finally:
        session.close()
    return stats


def run(
    input_path: Path,
    output_path: Path,
    visit: bool = True,
    use_search: bool = True,
    use_finnrick: bool = True,
    limit: Optional[int] = None,
    resume: bool = False,
    do_import: bool = False,
    threshold: float = 0.55,
    keywords_file: Path = DEFAULT_KEYWORDS_FILE,
    map_file: Path = DEFAULT_MAP_FILE,
    pause_seconds: float = 0.5,
) -> dict:
    with input_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    name_col = name_column(fieldnames)
    if limit is not None:
        rows = rows[:limit]

    out_fields = fieldnames + [c for c in FILL_COLUMNS + ADDED_COLUMNS if c not in fieldnames]
    done = load_done_rows(output_path, name_col) if resume else {}

    finnrick = FinnrickClient() if use_finnrick else None
    if finnrick is not None:
        try:
            logger.info("Finnrick index loaded: %d vendors", finnrick.index_size())
        except requests.RequestException as exc:
            logger.warning("Finnrick unavailable (%s); continuing without it", exc)
            finnrick = None
    domain_map = {normalize_name(n): d for n, (_, d) in load_domain_map(map_file).items()}
    resolver = first_configured_provider() if use_search else None
    logger.info("website search: %s", resolver.name if resolver else "off")
    # The tracked list names specific compounds (for search queries); for
    # "does this site deal in peptides at all" the generic word counts too.
    keywords = (load_keywords(keywords_file) + ["peptide"]) if visit else []

    stats = {"rows": len(rows), "reused": 0, "website_filled": 0, "email_filled": 0,
             "phone_filled": 0, "reached": 0, "unreachable": 0, "no_website": 0}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    enriched: List[dict] = []

    try:
        with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=out_fields, extrasaction="ignore")
            writer.writeheader()
            for i, row in enumerate(rows, 1):
                name = row.get(name_col, "")
                if name in done:
                    result = done[name]
                    stats["reused"] += 1
                else:
                    before = dict(row)
                    result = enrich_row(row, name_col, finnrick, domain_map, resolver, visit, keywords, threshold)
                    for column, key in (("Website", "website_filled"), ("Email", "email_filled"), ("Phone", "phone_filled")):
                        if not before.get(column) and result.get(column):
                            stats[key] += 1
                    status = result.get("Site Status", "")
                    if status.startswith("reached"):
                        stats["reached"] += 1
                    elif status == "unreachable":
                        stats["unreachable"] += 1
                    elif status == "no website":
                        stats["no_website"] += 1
                    logger.info("%3d/%d %-36s site=%-42s email=%-32s %s", i, len(rows), name[:36],
                                (result.get("Website") or "-")[:42], (result.get("Email") or "-")[:32], status)
                    if visit and result.get("Website"):
                        time.sleep(pause_seconds)
                writer.writerow({c: result.get(c, "") for c in out_fields})
                fh.flush()
                enriched.append(result)
    finally:
        shutdown_browser()

    if do_import:
        stats["crm"] = import_to_crm(enriched, name_col)

    logger.info("Done. %s -> %s", stats, output_path)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="CSV with at least a Vendor/Name/Company column")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output CSV (default: data/out/<input name>_enriched.csv)")
    parser.add_argument("--no-visit", action="store_true", help="Don't crawl vendor sites; directory sources only")
    parser.add_argument("--no-search", action="store_true", help="Don't web-search for missing websites")
    parser.add_argument("--no-finnrick", action="store_true", help="Don't consult Finnrick's public vendor API")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N rows")
    parser.add_argument("--resume", action="store_true", help="Keep rows already completed in --out")
    parser.add_argument("--import-crm", action="store_true", help="Also load the enriched rows into the CRM")
    parser.add_argument("--threshold", type=float, default=0.55, help="Name/domain similarity needed to accept a search hit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")
    if not args.input.exists():
        sys.exit(f"No such file: {args.input}")
    output = args.out or (config.DATA_DIR / "out" / f"{args.input.stem}_enriched.csv")
    run(
        input_path=args.input,
        output_path=output,
        visit=not args.no_visit,
        use_search=not args.no_search,
        use_finnrick=not args.no_finnrick,
        limit=args.limit,
        resume=args.resume,
        do_import=args.import_crm,
        threshold=args.threshold,
    )


if __name__ == "__main__":
    main()
