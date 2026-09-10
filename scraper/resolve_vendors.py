"""Resolve vendor company NAMES to their actual company websites.

A directory listing gives you names and profile links on the directory's own
domain -- not the companies' real sites. This runs one search per name and
picks the most likely official domain, then appends the results to the seed
URL list for the agent to process.

Usage:
    python -m scraper.resolve_vendors --country "United States" --dry-run
    python -m scraper.resolve_vendors --country "United States" --limit 40
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional, Tuple

import requests

from scraper.import_list import _is_company_host, _normalize_host, merge_into_seed_file
from scraper.search_providers import (
    BraveSearchProvider,
    DuckDuckGoProvider,
    GoogleCustomSearchProvider,
    SerperProvider,
)

logger = logging.getLogger(__name__)

DEFAULT_NAMES_FILE = Path(__file__).parent / "vendor_names.tsv"
DEFAULT_SEED_FILE = Path(__file__).parent / "seed_urls.txt"
DEFAULT_MAP_FILE = Path(__file__).parent / "vendor_domains.tsv"

# Directories, marketplaces and press -- never the vendor's own site.
NOT_THE_VENDOR = {
    "peptidebase.io", "thepeptidelist.com", "reddit.com", "amazon.com",
    "ebay.com", "trustpilot.com", "yelp.com", "crunchbase.com", "bloomberg.com",
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com", "x.com",
    "wikipedia.org", "medium.com", "substack.com", "quora.com", "glassdoor.com",
}


def load_vendor_names(path: Path) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        name = parts[0].strip()
        country = parts[1].strip() if len(parts) > 1 else ""
        if name:
            rows.append((name, country))
    return rows


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def score_domain(name: str, host: str) -> float:
    """How likely `host` is the official site for company `name`.

    Compares the registrable domain's label against the company name with
    punctuation and spacing removed, so "Blue Sky Peptide" matches
    "blueskypeptide.com" strongly and "somerandomblog.com" weakly.
    """
    label = host.split(".")[0]
    return SequenceMatcher(None, _slug(name), _slug(label)).ratio()


def pick_domain(name: str, urls: List[str], threshold: float) -> Optional[str]:
    best: Optional[str] = None
    best_score = 0.0
    for position, url in enumerate(urls):
        host = _normalize_host(re.sub(r"^https?://", "", url).split("/")[0])
        if not _is_company_host(host) or host in NOT_THE_VENDOR:
            continue
        # Rank mostly by name similarity, with a nudge toward higher results.
        score = score_domain(name, host) + max(0.0, (5 - position)) * 0.01
        if score > best_score:
            best, best_score = host, score
    return best if best_score >= threshold else None


def first_configured_provider():
    """Best available search provider: a keyed API first, DuckDuckGo last."""
    for provider in (SerperProvider(), BraveSearchProvider(), GoogleCustomSearchProvider(), DuckDuckGoProvider()):
        if provider.is_configured():
            return provider
    return None


def load_domain_map(path: Path) -> dict:
    """name -> (country, domain) for vendors already resolved."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            out[parts[0]] = (parts[1], parts[2])
    return out


def write_domain_map(rows: List[Tuple[str, str, str]], path: Path) -> None:
    """Merge new name/country/domain triples into the map file."""
    existing = load_domain_map(path)
    for name, country, domain in rows:
        existing[name] = (country, domain)
    lines = ["# name\tcountry\tresolved domain -- written by resolve_vendors.py"]
    lines += [f"{name}\t{country}\t{domain}" for name, (country, domain) in sorted(existing.items())]
    path.write_text("\n".join(lines) + "\n")


def resolve(
    names_file: Path,
    seed_file: Path,
    country: Optional[str],
    limit: Optional[int],
    threshold: float,
    dry_run: bool,
    map_file: Path = DEFAULT_MAP_FILE,
) -> dict:
    provider = first_configured_provider()
    if provider is None:
        sys.exit("No search provider available. Set SERPER_API_KEY (or BRAVE/GOOGLE) in .env, "
                 "or `pip install ddgs` for the keyless fallback")

    vendors = load_vendor_names(names_file)
    if country:
        vendors = [(n, c) for n, c in vendors if c.lower() == country.lower()]
    if limit is not None:
        vendors = vendors[:limit]

    logger.info("Resolving %d vendor names via %s", len(vendors), provider.name)

    resolved: List[str] = []
    unresolved: List[str] = []
    mapping: List[Tuple[str, str, str]] = []

    for name, vendor_country in vendors:
        query = f'"{name}" peptides official site'
        try:
            urls = [r.url for r in provider.search(query, 6)]
        except requests.RequestException as exc:
            logger.warning("search failed for %r: %s", name, exc)
            unresolved.append(name)
            continue

        host = pick_domain(name, urls, threshold)
        if host:
            logger.info("  %-32s -> %s", name, host)
            resolved.append(host)
            mapping.append((name, vendor_country, host))
        else:
            logger.info("  %-32s -> (no confident match)", name)
            unresolved.append(name)
        time.sleep(0.2)

    print(f"\nResolved {len(resolved)} of {len(vendors)} names; {len(unresolved)} unresolved")

    if dry_run:
        print("\n(dry run -- nothing written)")
    else:
        write_domain_map(mapping, map_file)
        added, already = merge_into_seed_file(sorted(set(resolved)), seed_file)
        print(f"Added {len(added)} new domains to {seed_file} ({already} already present)")
        if added:
            print(f"\nNow run:\n  python -m scraper.agent --seed-urls-file {seed_file}")

    if unresolved:
        print(f"\nCould not confidently resolve ({len(unresolved)}): {', '.join(unresolved[:20])}"
              + (" ..." if len(unresolved) > 20 else ""))

    return {"resolved": len(resolved), "unresolved": len(unresolved)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--names-file", type=Path, default=DEFAULT_NAMES_FILE)
    parser.add_argument("--seed-file", type=Path, default=DEFAULT_SEED_FILE)
    parser.add_argument("--map-file", type=Path, default=DEFAULT_MAP_FILE)
    parser.add_argument("--country", default="United States", help='Filter by country; "" for all')
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.55,
        help="Minimum name/domain similarity to accept a match (0-1, default 0.55)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )
    resolve(args.names_file, args.seed_file, args.country or None, args.limit, args.threshold,
            args.dry_run, args.map_file)


if __name__ == "__main__":
    main()
