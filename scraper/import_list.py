"""Import candidate company URLs from a page you saved or copied yourself.

Some directory sites block automated access (Cloudflare challenges, 403s).
When you can view such a page in your own browser but a script can't fetch
it, save or copy it and run it through here: this extracts company domains
and appends them to the seed URL list, which the agent then processes
normally.

Usage:
    # from a saved page (Chrome: Ctrl+S -> "Webpage, Complete" or "HTML Only")
    python -m scraper.import_list saved-page.html

    # or from text/links pasted into a file
    python -m scraper.import_list pasted.txt

    # preview without writing
    python -m scraper.import_list saved-page.html --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Set
from urllib.parse import urlparse

DEFAULT_SEED_FILE = Path(__file__).parent / "seed_urls.txt"

URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b", re.IGNORECASE)

# Hosts that appear on directory pages but are never the lead itself.
NON_COMPANY_HOSTS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "tiktok.com", "reddit.com", "pinterest.com", "t.me",
    "google.com", "gstatic.com", "googleapis.com", "gravatar.com",
    "cloudflare.com", "cloudflareinsights.com", "jsdelivr.net", "unpkg.com",
    "wordpress.org", "w3.org", "schema.org", "shopify.com", "wix.com",
    "squarespace.com", "gmail.com", "example.com", "sentry.io", "stripe.com",
    "paypal.com", "trustpilot.com", "archive.org", "cdn.shopify.com",
}

NON_COMPANY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".ico", ".woff", ".woff2")


def _normalize_host(host: str) -> str:
    host = host.lower().strip().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _is_company_host(host: str) -> bool:
    if not host or "." not in host:
        return False
    if host.endswith(NON_COMPANY_SUFFIXES):
        return False
    # Match the registrable domain so subdomains of excluded hosts drop too.
    parts = host.split(".")
    registrable = ".".join(parts[-2:]) if len(parts) >= 2 else host
    return registrable not in NON_COMPANY_HOSTS and host not in NON_COMPANY_HOSTS


def extract_domains(content: str, source_host: str | None = None) -> List[str]:
    """Pull candidate company domains out of raw HTML or pasted text."""
    hosts: Set[str] = set()

    for url in URL_RE.findall(content):
        host = _normalize_host(urlparse(url).netloc)
        if _is_company_host(host):
            hosts.add(host)

    # Directory listings often show a bare domain as the visible label.
    for match in BARE_DOMAIN_RE.findall(content):
        host = _normalize_host(match)
        if _is_company_host(host):
            hosts.add(host)

    if source_host:
        hosts.discard(_normalize_host(source_host))

    return sorted(hosts)


def merge_into_seed_file(domains: List[str], seed_file: Path) -> tuple[List[str], int]:
    """Append domains not already present. Returns (added, already_present)."""
    existing_hosts: Set[str] = set()
    if seed_file.exists():
        for line in seed_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                existing_hosts.add(_normalize_host(urlparse(line).netloc or line))

    new = [d for d in domains if d not in existing_hosts]
    if new:
        with seed_file.open("a") as fh:
            for domain in new:
                fh.write(f"https://{domain}/\n")
    return new, len(domains) - len(new)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input_file", type=Path, help="Saved .html page or a .txt file of pasted content")
    parser.add_argument("--seed-file", type=Path, default=DEFAULT_SEED_FILE)
    parser.add_argument("--source-host", default=None, help="Directory's own host, to exclude it from results")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be added, write nothing")
    args = parser.parse_args()

    if not args.input_file.exists():
        sys.exit(f"No such file: {args.input_file}")

    content = args.input_file.read_text(errors="replace")
    domains = extract_domains(content, args.source_host)
    print(f"Found {len(domains)} candidate company domains in {args.input_file}")

    if args.dry_run:
        for domain in domains:
            print("  ", domain)
        return

    added, already = merge_into_seed_file(domains, args.seed_file)
    print(f"Added {len(added)} new to {args.seed_file} ({already} already present)")
    for domain in added:
        print("  +", domain)
    if added:
        print(f"\nNow run:\n  python -m scraper.agent --seed-urls-file {args.seed_file}")


if __name__ == "__main__":
    main()
