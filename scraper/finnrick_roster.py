"""Append Finnrick's vendor roster to a vendor spreadsheet.

Finnrick's public index lists every vendor it has ever tracked (about 1,800
at the time of writing), most with a website. This takes a vendor CSV like
the one enrich_csv completes, adds one row per Finnrick vendor that is not
already in it, and writes the combined file -- ready for enrich_csv to fill
in emails and the rest.

Rows are added with the name, country (from Finnrick's free-text location),
"Listed On: Finnrick", the profile link, the website Finnrick has on file,
and a note with trading status and test count. Vendors Finnrick marks as
not found, deactivated or archived are left out. Tested vendors come first,
so a --limit still gets the ones with the most evidence behind them.

Usage:
    python -m scraper.finnrick_roster vendors.csv --out combined.csv
    python -m scraper.finnrick_roster vendors.csv --out combined.csv --only-tested
    python -m scraper.finnrick_roster vendors.csv --out combined.csv --limit 300
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from scraper.finnrick import FinnrickClient, is_vendor_site, normalize_name, slug_from_profile_url

logger = logging.getLogger(__name__)

SKIP_STATUSES = {"not found", "deactivated", "archived"}

_COUNTRY_ALIASES = {
    "us": "United States", "usa": "United States", "u.s.": "United States", "u.s.a.": "United States",
    "united states": "United States", "america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "united kingdom": "United Kingdom",
    "england": "United Kingdom", "gb": "United Kingdom",
    "cn": "China", "china": "China", "prc": "China",
    "ca": "Canada", "canada": "Canada", "au": "Australia", "australia": "Australia",
    "nz": "New Zealand", "eu": "European Union", "hk": "Hong Kong", "hong kong": "Hong Kong",
}


def location_to_country(location: Optional[str]) -> str:
    """Finnrick's location is free text ("US Mi, Fl", "china", "Us"); reduce
    it to a country name where that is unambiguous, else pass it through."""
    text = (location or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[lowered]
    first = re.split(r"[\s,/;-]+", lowered, maxsplit=1)[0]
    if first in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[first]
    return text[0].upper() + text[1:]


def website_from_entry(entry: dict) -> str:
    """The vendor's own site from the index entry, or "" -- the field can
    hold several URLs separated by whitespace, or a forum/Telegram link."""
    if (entry.get("contact_kind") or "website") != "website":
        return ""
    for token in (entry.get("contact_url") or "").split():
        if is_vendor_site(token):
            return token if "://" in token else f"https://{token}/"
    return ""


def finnrick_rows(index_entries: List[dict], existing_rows: List[dict], name_col: str) -> List[dict]:
    have_slugs = {slug_from_profile_url(r.get("Finnrick Profile", "")) for r in existing_rows}
    have_names = {normalize_name(r.get(name_col, "")) for r in existing_rows}
    rows: List[dict] = []
    for entry in index_entries:
        slug = entry.get("slug")
        name = (entry.get("name") or "").strip()
        if not slug or not name:
            continue
        if slug in have_slugs or normalize_name(name) in have_names:
            continue
        if (entry.get("status") or "").lower() in SKIP_STATUSES:
            continue
        tests = int(entry.get("test_count") or 0)
        note = f"Finnrick status: {entry.get('status') or 'unknown'}; tests: {tests}"
        if entry.get("latest_test_display"):
            note += f"; latest test {entry['latest_test_display']}"
        rows.append({
            name_col: name,
            "Country": location_to_country(entry.get("location")),
            "Listed On": "Finnrick",
            "Finnrick Profile": f"https://www.finnrick.com/vendors/{slug}",
            "Website": website_from_entry(entry),
            "Notes": note,
            "_tests": tests,
        })
    rows.sort(key=lambda r: (-r["_tests"], r[name_col].lower()))
    for r in rows:
        del r["_tests"]
    return rows


def run(input_path: Path, output_path: Path, only_tested: bool = False, limit: Optional[int] = None,
        client: Optional[FinnrickClient] = None) -> dict:
    with input_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        existing = list(reader)
    from scraper.enrich_csv import name_column

    name_col = name_column(fieldnames)
    client = client or FinnrickClient()
    entries = list(client._load_index().values())
    new_rows = finnrick_rows(entries, existing, name_col)
    if only_tested:
        new_rows = [r for r in new_rows if "tests: 0" not in r["Notes"]]
    if limit is not None:
        new_rows = new_rows[:limit]

    out_fields = fieldnames + [c for c in ("Country", "Listed On", "Finnrick Profile", "Website", "Notes") if c not in fieldnames]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        for row in existing + new_rows:
            writer.writerow({c: row.get(c, "") for c in out_fields})
    stats = {"existing": len(existing), "added": len(new_rows), "finnrick_total": len(entries)}
    logger.info("Wrote %s: %s", output_path, stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only-tested", action="store_true", help="Only vendors Finnrick has test results for")
    parser.add_argument("--limit", type=int, default=None, help="Add at most N vendors (tested ones first)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    if not args.input.exists():
        sys.exit(f"No such file: {args.input}")
    stats = run(args.input, args.out, args.only_tested, args.limit)
    print(f"Added {stats['added']} Finnrick vendors to {stats['existing']} existing rows -> {args.out}")
    print(f"\nNow run:\n  python -m scraper.enrich_csv {args.out} --out <completed.csv> --workers 6")


if __name__ == "__main__":
    main()
