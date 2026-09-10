"""Visit each candidate's website, pull emails/phones/instagram, and score for peptides.

Strategy per site:
  1. requests + BeautifulSoup on homepage + up to MAX_PAGES likely pages (contact, about, services, peptides...)
  2. If blocked (403/503/Cloudflare) or the page is JS-only, retry with headless Chromium (Playwright).
"""
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .keywords import score, classify

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
MAX_PAGES = int(os.getenv("MAX_PAGES_PER_SITE", "6"))
DELAY = float(os.getenv("REQUEST_DELAY_SECONDS", "1.5"))
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
IG_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)")
PRIORITY_PATHS = ["contact", "contact-us", "about", "about-us", "services", "peptides",
                  "peptide-therapy", "wholesale", "menu", "treatments", "weight-loss", "nad"]
JUNK_EMAIL = ("example.com", "sentry", "wixpress", "domain.com", "email.com", "yourdomain",
              ".png", ".jpg", ".gif", ".svg", "godaddy", "wordpress", "squarespace", "noreply", "no-reply")

_pw = None
_browser = None


def _browser_get(url: str, storage_state=None):
    """Headless Chromium fetch. Reuses one browser for the whole run."""
    global _pw, _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    if _browser is None:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(headless=HEADLESS)
    ctx_kwargs = {"user_agent": UA}
    if storage_state and os.path.exists(storage_state):
        ctx_kwargs["storage_state"] = storage_state
    ctx = _browser.new_context(**ctx_kwargs)
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(1)
        html = page.content()
    except Exception:
        html = ""
    finally:
        ctx.close()
    return html


def shutdown_browser():
    global _pw, _browser
    if _browser:
        _browser.close()
        _pw.stop()
        _browser = _pw = None


def fetch(url: str, storage_state=None) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        blocked = r.status_code in (403, 429, 503) or "cf-browser-verification" in r.text or "Just a moment" in r.text
        if not blocked and r.status_code == 200 and len(r.text) > 500:
            return r.text
    except requests.RequestException:
        pass
    return _browser_get(url, storage_state)


def _clean_emails(emails):
    out = set()
    for e in emails:
        e = e.strip().strip(".").lower()
        if any(j in e for j in JUNK_EMAIL) or len(e) > 60:
            continue
        out.add(e)
    return sorted(out)


def _clean_phones(phones):
    out = set()
    for p in phones:
        digits = re.sub(r"\D", "", p)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10 and not digits.startswith(("0", "1")):
            out.add(f"({digits[:3]}) {digits[3:6]}-{digits[6:]}")
    return sorted(out)


def extract(html: str, base_url: str):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = soup.get_text(" ", strip=True)
    emails = set(EMAIL_RE.findall(html))          # raw html catches mailto: + obfuscated
    for a in soup.select("a[href^=mailto]"):
        emails.add(a["href"][7:].split("?")[0])
    phones = set(PHONE_RE.findall(text))
    for a in soup.select("a[href^=tel]"):
        phones.add(a["href"][4:])
    ig = IG_RE.findall(html)
    links = []
    base_host = urlparse(base_url).netloc.replace("www.", "")
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(href).netloc.replace("www.", "") == base_host:
            links.append(href)
    title = soup.title.get_text(strip=True) if soup.title else ""
    return text, emails, phones, ig, links, title


def enrich(rec: dict) -> dict:
    site = rec.get("website") or f"https://{rec['domain']}"
    if not site.startswith("http"):
        site = "https://" + site
    all_text, emails, phones, igs, contact_page = [], set(), set(), [], ""
    visited, queue = set(), [site]
    html = fetch(site)
    if not html:
        rec.update({"confidence": "site_unreachable", "peptide_hits": 0, "notes": (rec.get("notes") or "") + " | site unreachable"})
        return rec
    text, e, p, ig, links, title = extract(html, site)
    all_text.append(text); emails |= e; phones |= p; igs += ig
    if not rec.get("business_name") and title:
        rec["business_name"] = title.split(" | ")[0].split(" - ")[0].strip()
    visited.add(site)
    # Prioritise contact/about/peptide pages
    ranked = sorted(set(links), key=lambda u: min((i for i, k in enumerate(PRIORITY_PATHS) if k in u.lower()), default=99))
    for u in ranked[:MAX_PAGES - 1]:
        if u in visited:
            continue
        visited.add(u)
        time.sleep(DELAY)
        h = fetch(u)
        if not h:
            continue
        t, e, p, ig, _, _ = extract(h, u)
        if e and not contact_page and ("contact" in u.lower() or "about" in u.lower()):
            contact_page = u
        all_text.append(t); emails |= e; phones |= p; igs += ig
    s = score(" ".join(all_text))
    emails_c = _clean_emails(emails)
    phones_c = _clean_phones(phones)
    rec["emails"] = "; ".join(emails_c)
    if phones_c and not rec.get("phones"):
        rec["phones"] = "; ".join(phones_c[:3])
    if igs:
        rec["instagram"] = "@" + igs[0]
    rec["peptides_found"] = s["peptides_found"]
    rec["peptide_hits"] = s["peptide_hits"]
    rec["contact_page"] = contact_page
    if not rec.get("category") or rec["category"] in ("", "unknown"):
        rec["category"] = classify(s)
    rec["confidence"] = ("high" if s["peptide_hits"] >= 3 and emails_c else
                         "medium" if s["peptide_hits"] >= 1 else
                         "low_no_peptide_mention")
    return rec


def crawl_directory(url: str, storage_state=None):
    """Return all external business links found on a directory/listing page (+ pagination up to 10 pages)."""
    from .discover import domain_of, _skip
    found, seen_pages, page_url, n = {}, set(), url, 0
    base_dom = domain_of(url)
    while page_url and n < 10 and page_url not in seen_pages:
        seen_pages.add(page_url); n += 1
        html = fetch(page_url, storage_state)
        if not html:
            break
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = urljoin(page_url, a["href"])
            dom = domain_of(href)
            if dom and dom != base_dom and not _skip(dom) and dom not in found:
                found[dom] = {"business_name": a.get_text(strip=True)[:80], "website": href, "domain": dom,
                              "source": f"directory:{base_dom}", "source_query": url}
        nxt = soup.find("a", string=re.compile(r"next|›|»", re.I)) or soup.select_one("a[rel=next]")
        page_url = urljoin(page_url, nxt["href"]) if nxt and nxt.get("href") else None
        time.sleep(DELAY)
    return list(found.values())
