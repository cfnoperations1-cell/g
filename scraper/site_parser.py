"""Fetches a candidate company's own public website and extracts lead info.

Only ever visits a handful of a site's own public pages (home/about/contact),
respects robots.txt, identifies itself with a descriptive User-Agent, and
pauses between requests — this is a polite, low-volume crawler, not a
high-throughput scraper.

Pages that come back as an empty JavaScript shell are optionally re-fetched
with headless Chromium (Playwright) so JS-rendered sites still yield their
contact details. The browser uses the same User-Agent and the same
robots.txt decision as the plain fetch; it is a rendering fallback, not a
way around a site's wishes.
"""
from __future__ import annotations

import atexit
import ipaddress
import logging
import os
import re
import socket
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
INSTAGRAM_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})")
_INSTAGRAM_NOT_HANDLES = {"p", "explore", "reel", "reels", "accounts", "stories", "share", "tv", "direct"}

# Responses that mean "a browser might get through where a plain GET did
# not": bot challenges and outright JS-only shells.
_BLOCKED_STATUSES = {403, 429, 503}
_CHALLENGE_MARKERS = ("cf-browser-verification", "just a moment", "checking your browser",
                      "enable javascript and cookies", "attention required")
_MIN_USEFUL_TEXT_CHARS = 200

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
    instagram: Optional[str] = None
    pages_checked: List[str] = field(default_factory=list)
    used_browser: bool = False


def get_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def is_public_host(domain: str) -> bool:
    """True only if the hostname resolves exclusively to public addresses.

    Candidate URLs come from search results and user-supplied lists, so
    without this check the scraper could be steered into fetching internal
    services or a cloud provider's metadata endpoint (SSRF). Any hostname
    that resolves to a loopback, private, link-local, or otherwise reserved
    address is refused outright.
    """
    try:
        infos = socket.getaddrinfo(domain, None)
    except (socket.gaierror, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not addr.is_global:
            return False
    return True


def _load_robots(domain: str) -> RobotFileParser:
    """Fetch and parse robots.txt with the same User-Agent used for pages.

    RobotFileParser.read() would fetch it with urllib's default UA, which
    many hosts answer with 403 -- and the parser then reads that as
    "disallow everything", locking us out of sites that permit crawling.
    Status handling follows RFC 9309: a 4xx means no restrictions, a 5xx
    means stay out, and a network failure is treated as no restrictions.
    """
    rp = RobotFileParser()
    try:
        resp = requests.get(
            f"https://{domain}/robots.txt",
            headers={"User-Agent": config.SCRAPER_USER_AGENT},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.debug("robots.txt unavailable for %s (%s); assuming no restrictions", domain, exc)
        rp.parse([])
        return rp
    if 400 <= resp.status_code < 500:
        rp.parse([])
    elif resp.status_code >= 500:
        rp.disallow_all = True
    else:
        rp.parse(resp.text.splitlines())
    return rp


def _allowed_by_robots(domain: str, path: str) -> bool:
    rp = _robots_cache.get(domain)
    if rp is None:
        rp = _load_robots(domain)
        _robots_cache[domain] = rp
    try:
        return rp.can_fetch(config.SCRAPER_USER_AGENT, f"https://{domain}{path}")
    except Exception:
        return True


_MAX_REDIRECTS = 5

# --- headless-browser fallback -------------------------------------------

_playwright = None
_browser = None  # None = not started, False = tried and unavailable
_browser_used_this_run = False


def browser_fallback_available() -> bool:
    if not config.BROWSER_FALLBACK:
        return False
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _start_browser():
    global _playwright, _browser
    if _browser is not None:
        return _browser
    try:
        from playwright.sync_api import sync_playwright

        _playwright = sync_playwright().start()
        kwargs = {"headless": True}
        if config.PLAYWRIGHT_CHROMIUM_PATH:
            kwargs["executable_path"] = config.PLAYWRIGHT_CHROMIUM_PATH
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            kwargs["proxy"] = {"server": proxy}
        _browser = _playwright.chromium.launch(**kwargs)
    except Exception as exc:  # missing binary, sandbox restrictions, ...
        logger.warning("browser fallback unavailable: %s", str(exc).splitlines()[0][:200])
        _browser = False
    return _browser


def shutdown_browser() -> None:
    """Close the shared headless browser, if one was started."""
    global _playwright, _browser
    try:
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()
    except Exception:
        pass
    _browser = None
    _playwright = None


atexit.register(shutdown_browser)


def _browser_fetch(url: str) -> Optional[str]:
    """Render a page in headless Chromium and return its HTML.

    The browser follows redirects itself, so the landing URL is re-checked
    against is_public_host afterwards and the content discarded if it ended
    up somewhere non-public.
    """
    global _browser_used_this_run
    browser = _start_browser()
    if not browser:
        return None
    context = browser.new_context(user_agent=config.SCRAPER_USER_AGENT)
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        landed = get_domain(page.url)
        if not landed or not is_public_host(landed):
            logger.warning("browser landed on non-public host %r; discarding", landed)
            return None
        _browser_used_this_run = True
        return page.content()
    except Exception as exc:
        logger.debug("browser fetch failed for %s: %s", url, str(exc).splitlines()[0][:160])
        return None
    finally:
        context.close()


def _looks_like_challenge(html: str) -> bool:
    lowered = html[:5000].lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def _is_thin_page(html: str) -> bool:
    """True for a JS-app shell: markup with almost no readable text."""
    try:
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    except Exception:
        return False
    return len(text) < _MIN_USEFUL_TEXT_CHARS


def _fetch(url: str) -> Optional[str]:
    """GET a page, following redirects by hand so every hop is re-checked
    against is_public_host -- a public site 301ing to an internal address
    must not carry the request along with it.

    A bot-challenge response or an empty JS shell is retried once through
    the headless browser when that fallback is available."""
    headers = {"User-Agent": config.SCRAPER_USER_AGENT}
    html: Optional[str] = None
    retry_in_browser = False
    try:
        for _ in range(_MAX_REDIRECTS):
            resp = requests.get(
                url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS, allow_redirects=False
            )
            if resp.is_redirect or resp.is_permanent_redirect:
                url = urljoin(url, resp.headers.get("Location", ""))
                next_host = get_domain(url)
                if not next_host or not is_public_host(next_host):
                    logger.warning("refusing redirect to non-public host: %r", next_host)
                    return None
                continue
            if resp.status_code in _BLOCKED_STATUSES:
                retry_in_browser = True
                break
            resp.raise_for_status()
            html = resp.text
            if _looks_like_challenge(html) or _is_thin_page(html):
                retry_in_browser = True
            break
        else:
            logger.debug("too many redirects for %s", url)
            return None
    except requests.RequestException as exc:
        logger.debug("Failed to fetch %s: %s", url, exc)
        return None

    if retry_in_browser and browser_fallback_available():
        rendered = _browser_fetch(url)
        if rendered and not _looks_like_challenge(rendered):
            return rendered
    if html is not None and _looks_like_challenge(html):
        return None
    return html


PLACEHOLDER_EMAIL_LOCALS = {"your", "youremail", "your-email", "email", "name", "username", "user"}


def is_usable_email(email: str) -> bool:
    """Reject addresses that aren't a real, company-owned contact: malformed
    strings, generic role accounts, site-builder placeholders, and unfilled
    template tokens (e.g. "your@email" or a percent-encoded "[email]")."""
    if "%" in email:
        return False
    # mailto: hrefs are not validated by the address regex on the way in, so
    # check the shape here rather than trusting the link.
    if not EMAIL_RE.fullmatch(email):
        return False
    local, _, domain = email.partition("@")
    if local.lower() in GENERIC_EMAIL_PREFIXES or local.lower() in PLACEHOLDER_EMAIL_LOCALS:
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


def extract_instagram(html: str) -> Optional[str]:
    """First Instagram profile handle linked from the page, as "@handle"."""
    for handle in INSTAGRAM_RE.findall(html):
        if handle.lower() not in _INSTAGRAM_NOT_HANDLES:
            return "@" + handle.rstrip(".")
    return None


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

    if not domain or not is_public_host(domain):
        logger.warning("refusing non-public or unresolvable host: %r", domain)
        return data

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
            # Rank mailto links and page-text addresses in one pool: a page can
            # carry a mangled mailto ("...@acme.come") alongside the correct
            # address in its text, and taking the first mailto would keep the typo.
            data.email = pick_best_email(mailto_candidates + EMAIL_RE.findall(html), domain)

        if data.phone is None:
            phone_match = PHONE_RE.search(text)
            if phone_match:
                data.phone = phone_match.group(0)

        if data.instagram is None:
            data.instagram = extract_instagram(html)

    full_text = " ".join(combined_text)
    data.research_only_evidence = find_research_only_evidence(full_text)

    is_us, state = guess_us_presence(full_text)
    data.us_based = is_us
    data.state = state

    data.sells_direct = sells_direct(full_text)
    data.manufactures = manufactures(full_text)
    data.is_content_site = is_content_site(full_text)
    data.used_browser = _browser_used_this_run

    if peptide_keywords and contains_any_keyword(full_text, peptide_keywords):
        data.company_type = classify_company_type(full_text, data.research_only_evidence)

    return data
