"""Append PeptiPrices' supplier list to a vendor spreadsheet.

peptiprices.com compares prices across peptide suppliers and links each
supplier's own website from its /suppliers index (robots.txt allows the
page). This adds one row per supplier not already in the CSV, with the
name and website, tagged "Listed On: PeptiPrices" -- enrich_csv then fills
in the rest.

Usage:
    python -m scraper.peptiprices_roster vendors.csv --out combined.csv
    python -m scraper.peptiprices_roster vendors.csv --out combined.csv --from-file saved-suppliers.html
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import config
from scraper.finnrick import is_vendor_site, normalize_name
from scraper.import_list import _is_company_host, _normalize_host

logger = logging.getLogger(__name__)

SUPPLIERS_URL = "https://www.peptiprices.com/suppliers"

# Link texts that are a call to action, not the supplier's name.
GENERIC_LINK_TEXT = {"buy", "buy now", "shop", "shop now", "visit", "visit site", "visit website", "website",
                     "link", "view", "view site", "more", "learn more", "go", "here", "click here", "store"}


def _name_from_host(host: str) -> str:
    label = host.split(".")[0]
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", label) if part)


def parse_suppliers(html: str) -> List[Tuple[str, str]]:
    """(name, website) per external supplier link, one per registrable host.

    The link text is used as the name when it reads like one; otherwise the
    name is derived from the domain ("blue-sky-peptide.com" -> "Blue Sky Peptide").
    """
    soup = BeautifulSoup(html, "lxml")
    by_host: Dict[str, Tuple[str, str]] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http"):
            continue
        host = _normalize_host(urlparse(href).netloc)
        if not host or "peptiprices" in host or not _is_company_host(host) or not is_vendor_site(href):
            continue
        text = a.get_text(" ", strip=True)
        looks_like_name = (bool(text) and len(text) <= 60 and "http" not in text
                           and "." not in text.split()[-1] and text.lower() not in GENERIC_LINK_TEXT)
        name = text if looks_like_name else _name_from_host(host)
        current = by_host.get(host)
        # Prefer a real link text over a derived name if we see one later.
        if current is None or (current[0] == _name_from_host(host) and looks_like_name):
            by_host[host] = (name, f"https://{host}/")
    return sorted(by_host.values(), key=lambda t: t[0].lower())


def fetch_suppliers_page() -> str:
    resp = requests.get(SUPPLIERS_URL, headers={"User-Agent": config.SCRAPER_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def run(input_path: Path, output_path: Path, html: Optional[str] = None) -> dict:
    from scraper.enrich_csv import name_column
    from scraper.site_parser import get_domain

    with input_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        existing = list(reader)
    name_col = name_column(fieldnames)
    have_names = {normalize_name(r.get(name_col, "")) for r in existing}
    have_hosts = {get_domain(r["Website"] if "://" in r["Website"] else "https://" + r["Website"])
                  for r in existing if r.get("Website")}

    suppliers = parse_suppliers(html if html is not None else fetch_suppliers_page())
    new_rows = []
    for name, website in suppliers:
        host = get_domain(website)
        if normalize_name(name) in have_names or host in have_hosts:
            continue
        new_rows.append({name_col: name, "Listed On": "PeptiPrices", "Website": website,
                         "Notes": "Linked from peptiprices.com/suppliers"})

    out_fields = fieldnames + [c for c in ("Listed On", "Website", "Notes") if c not in fieldnames]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        for row in existing + new_rows:
            writer.writerow({c: row.get(c, "") for c in out_fields})
    stats = {"existing": len(existing), "suppliers_on_page": len(suppliers), "added": len(new_rows)}
    logger.info("Wrote %s: %s", output_path, stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--from-file", type=Path, default=None, help="Parse a saved copy of the suppliers page instead of fetching")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.input.exists():
        sys.exit(f"No such file: {args.input}")
    html = args.from_file.read_text(errors="replace") if args.from_file else None
    stats = run(args.input, args.out, html)
    print(f"Added {stats['added']} PeptiPrices suppliers ({stats['suppliers_on_page']} on the page) -> {args.out}")


if __name__ == "__main__":
    main()
