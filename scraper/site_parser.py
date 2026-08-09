"""Fetches a candidate company's own public website and extracts lead info.

Only ever visits a handful of a site's own public pages (home/about/contact),
respects robots.txt, identifies itself with a descriptive User-Agent, and
pauses between requests — this is a polite, low-volume crawler, not a
high-throughput scraper.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

import config
from scraper.classify import (
    classify_company_type,
    contains_any_keyword,
    guess_us_presence,
    is_content_site,
    manufactures,
    sells_direct,
)

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")

# Phrases that indicate a supplier markets peptides as "for research use only",
# which is the compliance language this specific niche uses to distinguish
# lab/research sales from human-consumption sales.
RESEARCH_ONLY_PHRASES = [
    "for research use only",
    "research use only",
    "not for human consumption",
    "not intended for human",
    "research purposes only",
    "for laboratory research use only",
    "ruo",
]

CANDIDATE_PATHS = ["", "/about", "/about-us", "/contact", "/contact-us"]

GENERIC_EMAIL_PREFIXES = {"privacy", "abuse", "webmaster", "postmaster", "noreply", "no-reply"}

# Domains belonging to site builders/templates and error trackers rather than
# the company itself -- these show up as unfilled placeholders in page source.
PLACEHOLDER_EMAIL_DOMAINS = {
    "mysite.com", "example.com", "example.org", "domain.com", "yourdomain.com",
    "yoursite.com", "email.com", "sentry.io", "wixpress.com", "shopify.com",
    "godaddy.com", "squarespace.com", "test.com",
}

_robots_cache: dict = {}


@dataclass
class SiteData:
    url: str
    domain: str
    company_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None
    research_only_evidence: Optional[str] = None
    company_type: Optional[str] = None
    sells_direct: bool = False
    manufactures: bool = False
    is_content_site: bool = False
    us_based: bool = False
    state: Optional[str] = None
    pages_checked: List[str] = field(default_factory=list)


def get_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _allowed_by_robots(domain: str, path: str) -> bool:
    rp = _robots_cache.get(domain)
    if rp is None:
        rp = RobotFileParser()
        rp.set_url(f"https://{domain}/robots.txt")
        try:
            rp.read()
        except Exception:
            pass
        _robots_cache[domain] = rp
    try:
        return rp.can_fetch(config.SCRAPER_USER_AGENT, f"https://{domain}{path}")
    except Exception:
        return True


def _fetch(url: str) -> Optional[str]:
    headers = {"User-Agent": config.SCRAPER_USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        logger.debug("Failed to fetch %s: %s", url, exc)
        return None


def is_usable_email(email: str) -> bool:
    """Reject addresses that aren't a real, company-owned contact: generic
    role accounts, site-builder placeholders, and unfilled template tokens
    (e.g. a mailto of "[email]", which arrives percent-encoded)."""
    if "%" in email:
        return False
    local, _, domain = email.partition("@")
    if local.lower() in GENERIC_EMAIL_PREFIXES:
        return False
    if domain.lower() in PLACEHOLDER_EMAIL_DOMAINS:
        return False
    return True


ROLE_EMAIL_KEYWORDS = ("sales", "info", "contact", "support", "orders", "hello")


def pick_best_email(candidates: List[str], site_domain: Optional[str] = None) -> Optional[str]:
    """Choose the most likely real contact address.

    Prefers an address on the company's own domain, then a role account
    (sales@/info@/...), then the shortest -- the length tiebreak catches
    markup typos where a stray character trails a valid address, e.g. both
    "hello@acme.com" and "hello@acme.come" being present in the page.
    """
    usable = [e.strip() for e in candidates if is_usable_email(e.strip())]
    if not usable:
        return None

    def rank(email: str):
        domain = email.partition("@")[2].lower()
        on_site_domain = 0 if (site_domain and domain == site_domain.lower()) else 1
        is_role = 0 if any(k in email.lower() for k in ROLE_EMAIL_KEYWORDS) else 1
        return (on_site_domain, is_role, len(email))

    return sorted(usable, key=rank)[0]


def _extract_email(text: str, site_domain: Optional[str] = None) -> Optional[str]:
    return pick_best_email(EMAIL_RE.findall(text), site_domain)


def find_research_only_evidence(text: str) -> Optional[str]:
    lowered = text.lower()
    for phrase in RESEARCH_ONLY_PHRASES:
        idx = lowered.find(phrase)
        if idx != -1:
            start = max(0, idx - 40)
            end = min(len(text), idx + len(phrase) + 40)
            return text[start:end].strip()
    return None


def parse_site(base_url: str, peptide_keywords: Optional[List[str]] = None) -> SiteData:
    domain = get_domain(base_url)
    data = SiteData(url=base_url, domain=domain)
    combined_text = []

    for path in CANDIDATE_PATHS:
        request_path = "/" + path.lstrip("/") if path else "/"
        if not _allowed_by_robots(domain, request_path):
            logger.debug("robots.txt disallows %s%s", domain, request_path)
            continue

        page_url = urljoin(f"https://{domain}/", path.lstrip("/"))
        html = _fetch(page_url)
        if not html:
            continue
        data.pages_checked.append(page_url)

        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        combined_text.append(text)

        if data.company_name is None:
            og_site = soup.find("meta", property="og:site_name")
            if og_site and og_site.get("content"):
                data.company_name = og_site["content"].strip()
            elif soup.title and soup.title.string:
                data.company_name = soup.title.string.strip().split("|")[0].split(" - ")[0].strip()

        if data.description is None:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                data.description = meta_desc["content"].strip()

        if data.email is None:
            mailto_candidates = [
                a["href"].replace("mailto:", "").split("?")[0]
                for a in soup.find_all("a", href=True)
                if a["href"].lower().startswith("mailto:")
            ]
            data.email = pick_best_email(mailto_candidates, domain) or _extract_email(html, domain)

        if data.phone is None:
            phone_match = PHONE_RE.search(text)
            if phone_match:
                data.phone = phone_match.group(0)

    full_text = " ".join(combined_text)
    data.research_only_evidence = find_research_only_evidence(full_text)

    is_us, state = guess_us_presence(full_text)
    data.us_based = is_us
    data.state = state

    data.sells_direct = sells_direct(full_text)
    data.manufactures = manufactures(full_text)
    data.is_content_site = is_content_site(full_text)

    if peptide_keywords and contains_any_keyword(full_text, peptide_keywords):
        data.company_type = classify_company_type(full_text, data.research_only_evidence)

    return data
