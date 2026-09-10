"""Import PeptideBase (peptidebase.io) directory pages you saved from your browser.

peptidebase.io answers every non-browser request -- robots.txt included --
with a Cloudflare challenge, so nothing in here fetches it. Instead: open
the directory in your browser, save each page (Ctrl+S, "Webpage, Complete"
or "HTML Only"), and point this at the saved files. It reads the listing
cards (name, provider type, location, Google rating, regulatory flags) and,
for any provider *profile* pages you also saved, the company's own website.

What happens with the result:
  * everything parsed is written to one CSV for your own use
  * providers whose website is known become CRM leads (source "peptidebase"),
    with the directory's type / location / rating / flags kept in the lead
    notes; contact details are then filled in from the company's own site
    through the same polite, robots.txt-respecting fetch the rest of the
    scraper uses
  * providers with only a name are appended to the vendor roster
    (vendor_names.tsv) so `python -m scraper.import_vendors` can resolve
    them to a website by search and import them the usual way

Pages it understands:
  /directory/telehealth, /directory/clinics, /directory/compounding-pharmacies
      -- card listings, one <a href="/providers/<slug>"> per provider
  /research-vendors            -- a table, one row per vendor
  /providers/<slug>            -- a profile page with a "Visit website" link

Usage:
    python -m scraper.import_peptidebase saved/*.html --dry-run
    python -m scraper.import_peptidebase saved/                # CSV + CRM + roster
    python -m scraper.import_peptidebase saved/ --no-enrich    # don't visit company sites
    python -m scraper.import_peptidebase saved/ --csv-only     # just the spreadsheet
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import config
from scraper.import_list import _is_company_host, _normalize_host
from scraper.resolve_vendors import DEFAULT_NAMES_FILE, load_vendor_names

logger = logging.getLogger(__name__)

SITE = "https://peptidebase.io"
SITE_HOST = "peptidebase.io"
SOURCE = "peptidebase"
DEFAULT_CSV = config.DATA_DIR / "peptidebase_listings.csv"
CSV_FIELDS = ["category", "type", "name", "location", "rating", "reviews", "flag", "website", "profile_url"]

PROVIDER_TYPES = [
    "Pharmacy (503A)", "Pharmacy (503B)", "Pharmacy", "Telehealth", "Clinic", "Physician",
    "Blood Testing Lab", "Peptide Testing Lab", "Research Vendor",
]
FLAGS = ["Regulatory warning", "Under Review", "Program Suspended"]

COUNTRIES = [
    "United States", "USA", "Canada", "United Kingdom", "UK", "Australia", "New Zealand",
    "Mexico", "Ireland", "Germany", "Netherlands", "France", "Spain", "Italy", "Portugal",
    "Switzerland", "Austria", "Belgium", "Sweden", "Norway", "Denmark", "Finland", "Poland",
    "Czech Republic", "Latvia", "Lithuania", "Estonia", "Hungary", "Romania", "Bulgaria",
    "Greece", "Turkey", "Israel", "United Arab Emirates", "India", "China", "Hong Kong",
    "Singapore", "Japan", "South Korea", "Thailand", "Philippines", "Vietnam", "Malaysia",
    "Indonesia", "South Africa", "Brazil", "Argentina", "Colombia", "Chile", "Peru",
]
US_COUNTRIES = {"United States", "USA"}
_COUNTRY_ALT = "|".join(re.escape(c) for c in sorted(COUNTRIES, key=len, reverse=True))
# "Austin, United States" or a bare "United States". The city part is kept
# short (up to three words) so it can't swallow the company name in front.
LOCATION_RE = re.compile(r"(?:[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){0,2}, )?(?:%s)" % _COUNTRY_ALT)
COUNTRY_RE = re.compile(r"\b(?:%s)\b" % _COUNTRY_ALT)
# Words that, immediately before the last word of a city, are part of it.
CITY_PREFIXES = {
    "New", "Los", "San", "Santa", "Salt", "Lake", "Fort", "Ft.", "St.", "Saint", "Las", "El", "La",
    "Hong", "Kansas", "Oklahoma", "Long", "Palm", "West", "East", "North", "South", "Grand",
    "Colorado", "Virginia", "Jersey", "Baton", "Little", "Sioux", "Des", "Cape", "Coral", "Boca",
    "Daytona", "Panama", "Winston", "Rio", "Sao", "São", "Ann", "Mount", "Mt.", "Port", "Point",
}
RATING_RE = re.compile(r"^\d(?:\.\d)?$")
REVIEWS_RE = re.compile(r"^\(?([\d,]+)\)?(?:\s*(?:Google\s*)?reviews?)?$", re.I)
INLINE_RATING_RE = re.compile(r"(\d\.\d)\s*\(?([\d,]+)\)?\s*(?:Google)?\s*(?:reviews?)?", re.I)
# Chrome/Edge write this as the first line of every saved page.
SAVED_FROM_RE = re.compile(r"saved from url=\(\d+\)(\S+)")
NOISE_CHUNKS = {"google", "view", "view →", "→", "verified", "visit", "reviews", "review"}

CATEGORY_BY_PATH = {
    "/directory/telehealth": "telehealth",
    "/directory/clinics": "clinics",
    "/directory/compounding-pharmacies": "compounding_pharmacies",
    "/research-vendors": "research_vendors",
}
CATEGORY_BY_TYPE = {
    "Telehealth": "telehealth",
    "Clinic": "clinics",
    "Physician": "clinics",
    "Pharmacy (503A)": "compounding_pharmacies",
    "Pharmacy (503B)": "compounding_pharmacies",
    "Pharmacy": "compounding_pharmacies",
    "Research Vendor": "research_vendors",
    "Blood Testing Lab": "labs",
    "Peptide Testing Lab": "labs",
}
# The directory's provider types that map onto one of the CRM's company
# types with confidence. Everything else is left for site classification.
COMPANY_TYPE_BY_TYPE = {
    "Pharmacy (503A)": "compounding_pharmacy",
    "Pharmacy (503B)": "compounding_pharmacy",
    "Pharmacy": "compounding_pharmacy",
}


@dataclass
class Provider:
    name: str
    type: str = ""
    category: str = ""
    location: str = ""
    rating: str = ""
    reviews: str = ""
    flag: str = ""
    website: str = ""
    profile_url: str = ""

    @property
    def country(self) -> str:
        return self.location.rsplit(", ", 1)[-1].strip() if self.location else ""

    @property
    def us_based(self) -> bool:
        return self.country in US_COUNTRIES

    def note(self) -> str:
        parts = [p for p in (self.type, self.location) if p]
        if self.rating:
            parts.append(f"rating {self.rating}" + (f" ({self.reviews} Google reviews)" if self.reviews else ""))
        if self.flag:
            parts.append(self.flag)
        if self.profile_url:
            parts.append(self.profile_url)
        return "PeptideBase: " + " · ".join(parts)

    def merge(self, other: "Provider") -> None:
        """Fill in blanks from another record of the same provider."""
        for key, value in asdict(other).items():
            if value and not getattr(self, key):
                setattr(self, key, value)


# ---------------------------------------------------------------------------
# Page-level helpers
# ---------------------------------------------------------------------------
def _normalize_url(url: str, base: str = SITE) -> str:
    url = urljoin(base, url.split("#")[0].split("?")[0])
    parsed = urlparse(url)
    return f"https://{parsed.netloc.lower().removeprefix('www.')}{parsed.path.rstrip('/')}"


def is_challenge_page(html: str, soup: BeautifulSoup) -> bool:
    """A saved copy of Cloudflare's interstitial instead of the real page."""
    title = (soup.title.string or "") if soup.title and soup.title.string else ""
    return "just a moment" in title.lower() or "cf-mitigated" in html[:5000] or "challenge-platform" in html


def page_url(html: str, soup: BeautifulSoup, path: Optional[Path] = None) -> str:
    """Recover the URL a saved page came from."""
    m = SAVED_FROM_RE.search(html[:3000])
    if m:
        return m.group(1)
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        return canonical["href"]
    og = soup.find("meta", property="og:url")
    if og and og.get("content"):
        return og["content"]
    if path is not None:
        stem = path.stem.lower()
        for route, _ in CATEGORY_BY_PATH.items():
            if route.rsplit("/", 1)[-1] in stem:
                return urljoin(SITE, route)
    return ""


def category_for_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    for route, category in CATEGORY_BY_PATH.items():
        if path == route or path.startswith(route + "/"):
            return category
    return ""


def _is_external_company_link(href: str) -> bool:
    if not href.lower().startswith(("http://", "https://")):
        return False
    host = _normalize_host(urlparse(href).netloc)
    return bool(host) and host != SITE_HOST and not host.endswith("." + SITE_HOST) and _is_company_host(host)


def _clean_website(href: str) -> str:
    parsed = urlparse(href.split("#")[0])
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


# ---------------------------------------------------------------------------
# Listing cards (telehealth / clinics / compounding pharmacies)
# ---------------------------------------------------------------------------
def _parse_card_chunks(chunks: List[str], provider: Provider) -> List[str]:
    """Classify each separately-marked-up text run of a card. Returns the
    runs that were not recognised as anything, in order -- the first one is
    almost always the company name."""
    leftovers: List[str] = []
    for chunk in chunks:
        chunk = " ".join(chunk.split())
        if not chunk or chunk.lower() in NOISE_CHUNKS:
            continue
        if chunk in PROVIDER_TYPES and not provider.type:
            provider.type = chunk
        elif chunk in FLAGS and not provider.flag:
            provider.flag = chunk
        elif LOCATION_RE.fullmatch(chunk) and not provider.location:
            provider.location = chunk
        elif RATING_RE.fullmatch(chunk) and not provider.rating:
            provider.rating = chunk
        elif REVIEWS_RE.fullmatch(chunk) and provider.rating and not provider.reviews:
            provider.reviews = REVIEWS_RE.fullmatch(chunk).group(1).replace(",", "")
        else:
            leftovers.append(chunk)
    return leftovers


def _parse_card_text(text: str, provider: Provider) -> str:
    """Fallback for a card whose text arrives as one undifferentiated run:
    peel off the known pieces and treat whatever is left as the name."""
    rest = " ".join(text.split())
    for ptype in PROVIDER_TYPES:
        if rest.startswith(ptype):
            provider.type = provider.type or ptype
            rest = rest[len(ptype):].strip()
            break
    for flag in FLAGS:
        if flag in rest:
            provider.flag = provider.flag or flag
            rest = rest.replace(flag, " ")
    m = INLINE_RATING_RE.search(rest)
    if m:
        provider.rating = provider.rating or m.group(1)
        provider.reviews = provider.reviews or m.group(2).replace(",", "")
        rest = rest[:m.start()] + rest[m.end():]
    rest = re.sub(r"\bView\b\s*→?", " ", rest)
    rest = re.sub(r"\bGoogle\b", " ", rest)
    rest = " ".join(rest.split())

    # "<name> <city>, <country>" with nothing marking where the name stops
    # and the city starts. Anchor on the country (the last one mentioned),
    # take the word before the comma as the city, and pull in preceding
    # words only when they are a known multi-word city prefix ("Salt Lake
    # City", "New York"). Everything before that is the name.
    countries = list(COUNTRY_RE.finditer(rest))
    if countries:
        m = countries[-1]
        before = rest[:m.start()].rstrip()
        rest = rest[m.end():]
        if before.endswith(","):
            words = before[:-1].split()
            city = [words.pop()] if words else []
            while words and words[-1] in CITY_PREFIXES:
                city.insert(0, words.pop())
            location = f"{' '.join(city)}, {m.group(0)}" if city else m.group(0)
            before = " ".join(words)
        else:
            location = m.group(0)
        provider.location = provider.location or location
        rest = f"{before} {rest}"
    return " ".join(rest.split()).strip(" -|·•→,")


def parse_listing_page(soup: BeautifulSoup, base: str = SITE) -> List[Provider]:
    """Directory cards: <a href="/providers/slug"> wrapping type, name,
    location, rating and a "View →" call to action."""
    candidates: Dict[str, List] = {}
    for a in soup.select('a[href*="/providers/"]'):
        href = a.get("href", "")
        url = _normalize_url(href, base)
        if urlparse(url).path.rstrip("/") in ("/providers", ""):
            continue  # the section link, not a card
        if not a.get_text(strip=True):
            continue  # image-only link to the same profile
        candidates.setdefault(url, []).append(a)

    out: List[Provider] = []
    for url, anchors in candidates.items():
        # A profile can be linked several times from one card (logo, name,
        # button); the anchor with the most text is the card itself.
        a = max(anchors, key=lambda tag: len(tag.get_text(" ", strip=True)))
        provider = Provider(name="", profile_url=url)
        heading = a.find(["h1", "h2", "h3", "h4", "strong"])
        chunks = list(a.stripped_strings)
        leftovers = _parse_card_chunks(chunks, provider)
        if heading and heading.get_text(strip=True):
            provider.name = " ".join(heading.get_text(" ", strip=True).split())
        elif len(chunks) == 1:
            provider.name = _parse_card_text(chunks[0], provider)
        elif leftovers:
            provider.name = leftovers[0]
        if not provider.name:
            continue
        provider.category = CATEGORY_BY_TYPE.get(provider.type, "")
        out.append(provider)
    return out


# ---------------------------------------------------------------------------
# Research-vendor table
# ---------------------------------------------------------------------------
def parse_vendor_table(soup: BeautifulSoup, base: str = SITE) -> List[Provider]:
    """/research-vendors lists vendors in a table, one row each. Columns are
    matched by header text when there is one, else by position."""
    out: List[Provider] = []
    seen = set()
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]

        def col(*keys: str) -> Optional[int]:
            for i, h in enumerate(headers):
                if any(k in h for k in keys):
                    return i
            return None

        loc_col, rating_col, flag_col = col("country", "location", "based"), col("rating", "score"), col("status", "flag", "warning")

        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells or tr.find("th") and not tr.find("td"):
                continue  # header row
            texts = [c.get_text(" ", strip=True) for c in cells]
            links = tr.find_all("a", href=True)
            profile = next((a for a in links if "/vendors/" in a["href"] or "/research-vendors/" in a["href"]), None)
            external = next((a["href"] for a in links if _is_external_company_link(a["href"])), "")
            name_tag = profile or (links[0] if links else None)
            name = name_tag.get_text(" ", strip=True) if name_tag else texts[0]
            if not name:
                continue
            key = _normalize_url(profile["href"], base) if profile else name.lower()
            if key in seen:
                continue
            seen.add(key)

            def cell(index: Optional[int], fallback: Optional[int] = None) -> str:
                for i in (index, fallback):
                    if i is not None and i < len(texts):
                        return texts[i]
                return ""

            location = cell(loc_col, 1 if loc_col is None else None)
            if not LOCATION_RE.search(location):
                location = next((t for t in texts if LOCATION_RE.fullmatch(t)), "")
            out.append(Provider(
                name=name, type="Research Vendor", category="research_vendors",
                location=location, rating=cell(rating_col), flag=cell(flag_col),
                website=_clean_website(external) if external else "",
                profile_url=_normalize_url(profile["href"], base) if profile else "",
            ))
    return out


# ---------------------------------------------------------------------------
# Provider profile page
# ---------------------------------------------------------------------------
def find_website(soup: BeautifulSoup) -> str:
    """The provider's own site, from a link labelled as such -- never just
    any outbound link, which on a profile page could be a map, a review
    site or an ad."""
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _is_external_company_link(href):
            continue
        label = a.get_text(" ", strip=True).lower()
        title = (a.get("title") or a.get("aria-label") or "").lower()
        host = _normalize_host(urlparse(href).netloc)
        if any(k in label or k in title for k in ("visit website", "official site", "website", "visit site")) \
                or label.removeprefix("www.").rstrip("/") == host:
            return _clean_website(href)
    return ""


def parse_profile_page(soup: BeautifulSoup, url: str) -> Optional[Provider]:
    name = ""
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        name = " ".join(h1.get_text(" ", strip=True).split())
    else:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            name = og["content"].split("|")[0].split(" - ")[0].strip()
        elif soup.title and soup.title.string:
            name = soup.title.string.split("|")[0].split(" - ")[0].strip()
    if not name:
        return None
    provider = Provider(name=name, profile_url=_normalize_url(url), website=find_website(soup))
    text = soup.get_text(" ", strip=True)
    for flag in FLAGS:
        if flag in text:
            provider.flag = flag
            break
    return provider


def _is_profile_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    return bool(re.match(r"^/(providers|vendors|research-vendors)/[^/]+$", path))


# ---------------------------------------------------------------------------
# Putting the pages together
# ---------------------------------------------------------------------------
def expand_inputs(inputs: Iterable[Path]) -> List[Path]:
    files: List[Path] = []
    for p in inputs:
        if p.is_dir():
            files.extend(sorted(f for f in p.iterdir() if f.suffix.lower() in (".html", ".htm", ".mhtml")))
        elif p.exists():
            files.append(p)
        else:
            logger.warning("no such file: %s", p)
    return files


def parse_file(path: Path) -> List[Provider]:
    html = path.read_text(errors="replace")
    soup = BeautifulSoup(html, "lxml")
    if is_challenge_page(html, soup):
        logger.warning("%s is Cloudflare's \"Just a moment...\" page, not the directory -- "
                       "open the page in your browser, wait for it to load, then save it", path.name)
        return []

    url = page_url(html, soup, path)
    if url and _is_profile_url(url):
        provider = parse_profile_page(soup, url)
        return [provider] if provider else []

    providers = parse_listing_page(soup)
    providers += parse_vendor_table(soup)
    category = category_for_url(url) if url else ""
    for provider in providers:
        provider.category = provider.category or category
    if not providers and url and not _is_profile_url(url):
        logger.warning("%s: no provider cards or vendor rows found", path.name)
    return providers


def load_providers(files: List[Path]) -> List[Provider]:
    """Parse every file and merge records of the same provider, so a saved
    profile page supplies the website for the card from a listing page."""
    by_key: Dict[str, Provider] = {}
    for path in files:
        for provider in parse_file(path):
            key = provider.profile_url or f"name:{provider.name.lower()}"
            if key in by_key:
                by_key[key].merge(provider)
            else:
                by_key[key] = provider
    return list(by_key.values())


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
def write_csv(providers: List[Provider], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for provider in sorted(providers, key=lambda p: (p.category, p.type, p.name.lower())):
            writer.writerow(asdict(provider))


def add_to_roster(providers: List[Provider], names_file: Path) -> List[str]:
    """Append name-only providers to vendor_names.tsv for resolve/import."""
    existing = {name.lower() for name, _ in load_vendor_names(names_file)}
    added: List[str] = []
    lines: List[str] = []
    for provider in providers:
        if provider.name.lower() in existing:
            continue
        existing.add(provider.name.lower())
        added.append(provider.name)
        lines.append(f"{provider.name}\t{provider.country}")
    if lines:
        with names_file.open("a") as fh:
            if names_file.stat().st_size and not names_file.read_text().endswith("\n"):
                fh.write("\n")
            fh.write("\n".join(lines) + "\n")
    return added


def upsert_lead(session, provider: Provider):
    """Create or update the CRM lead for a provider with a known website.
    Returns (status, lead) where status is added / updated / skipped."""
    from models import Lead
    from scraper.site_parser import get_domain

    domain = get_domain(provider.website)
    if not domain or not _is_company_host(domain):
        return "skipped", None

    company_type = COMPANY_TYPE_BY_TYPE.get(provider.type)
    lead = session.query(Lead).filter_by(domain=domain).one_or_none()
    if lead is None:
        lead = Lead(
            company_name=provider.name,
            website=provider.website,
            domain=domain,
            source=SOURCE,
            company_type=company_type,
            us_based=provider.us_based,
            notes=provider.note(),
            status="new",
        )
        session.add(lead)
        return "added", lead

    if not lead.source:
        lead.source = SOURCE
    elif SOURCE not in lead.source:
        lead.source = f"{lead.source}+{SOURCE}"
    if lead.company_type is None and company_type:
        lead.company_type = company_type
    lead.us_based = lead.us_based or provider.us_based
    if not lead.notes:
        lead.notes = provider.note()
    elif "PeptideBase:" not in lead.notes:
        lead.notes = f"{lead.notes}\n{provider.note()}"
    return "updated", lead


def run(
    inputs: List[Path],
    csv_path: Path = DEFAULT_CSV,
    names_file: Path = DEFAULT_NAMES_FILE,
    do_enrich: bool = True,
    dry_run: bool = False,
    csv_only: bool = False,
) -> dict:
    files = expand_inputs(inputs)
    providers = load_providers(files)
    with_site = [p for p in providers if p.website]
    name_only = [p for p in providers if not p.website]
    stats = {
        "pages": len(files), "providers": len(providers), "with_website": len(with_site),
        "added": 0, "updated": 0, "skipped": 0, "roster_added": 0, "enriched": 0, "unreachable": 0,
    }
    logger.info("%d pages -> %d providers (%d with a website)", len(files), len(providers), len(with_site))

    if dry_run:
        for p in sorted(providers, key=lambda p: (p.category, p.type, p.name.lower())):
            logger.info("[dry-run] %-24s %-16s %-32s %-28s %s", p.category, p.type, p.name[:32],
                        p.location[:28], p.website or f"(name only -> {names_file.name})")
        return stats

    if not providers:
        return stats

    write_csv(providers, csv_path)
    logger.info("wrote %s (%d rows)", csv_path, len(providers))
    if csv_only:
        return stats

    added_names = add_to_roster(name_only, names_file)
    stats["roster_added"] = len(added_names)
    if added_names:
        logger.info("%d name-only providers appended to %s; resolve them with: "
                    "python -m scraper.import_vendors", len(added_names), names_file)

    from db import SessionLocal, init_db

    init_db()
    peptide_keywords: List[str] = []
    if do_enrich:
        from scraper.agent import DEFAULT_KEYWORDS_FILE, load_keywords

        peptide_keywords = load_keywords(DEFAULT_KEYWORDS_FILE)

    session = SessionLocal()
    try:
        for provider in with_site:
            status, lead = upsert_lead(session, provider)
            stats[status] += 1
            if lead is not None and do_enrich and not lead.email:
                from scraper.import_vendors import enrich

                result = enrich(lead, peptide_keywords)
                stats["enriched" if result == "enriched" else "unreachable"] += 1
            session.commit()
    finally:
        session.close()

    logger.info("Done. %s", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path, help="Saved .html files and/or directories of them")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help=f"Where to write the spreadsheet (default {DEFAULT_CSV})")
    parser.add_argument("--names-file", type=Path, default=DEFAULT_NAMES_FILE,
                        help="Roster that name-only providers are appended to")
    parser.add_argument("--no-enrich", action="store_true", help="Don't visit company sites for contact details")
    parser.add_argument("--csv-only", action="store_true", help="Write the CSV and stop; touch neither CRM nor roster")
    parser.add_argument("--dry-run", action="store_true", help="Print what was parsed, write nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    if not expand_inputs(args.inputs):
        sys.exit("No saved pages found. Save peptidebase.io pages from your browser (Ctrl+S) first.")

    run(
        inputs=args.inputs,
        csv_path=args.csv,
        names_file=args.names_file,
        do_enrich=not args.no_enrich,
        dry_run=args.dry_run,
        csv_only=args.csv_only,
    )


if __name__ == "__main__":
    main()
