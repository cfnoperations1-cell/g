"""Finnrick (finnrick.com) public vendor API as a contact-data source.

Finnrick publishes independent test results for peptide vendors, and with
them a "contact channels" record per vendor: website(s), email, WhatsApp,
Telegram and so on. Its robots.txt explicitly allows crawling of the public
read API under /api/v1/ ("Finnrick publishes test data for public benefit"),
so unlike the gated directories this one can be read by a script.

Two endpoints are used:
  GET /api/v1/vendors          -> every vendor, with contacts inline
  GET /api/v1/vendors/<slug>   -> one vendor, same shape (used when the
                                  index entry has no contacts on it)

Usage from code:
    client = FinnrickClient()
    contacts = client.lookup(slug="aavant-research")      # or name="Aavant Research"
    contacts.website, contacts.emails, contacts.whatsapp
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

import config

logger = logging.getLogger(__name__)

INDEX_URL = "https://www.finnrick.com/api/v1/vendors"
VENDOR_URL = "https://www.finnrick.com/api/v1/vendors/{slug}"
PROFILE_RE = re.compile(r"finnrick\.com/vendors/([a-z0-9-]+)", re.IGNORECASE)

# A "website" contact on Finnrick is sometimes a forum thread or directory
# listing where the vendor trades, not the vendor's own site.
NOT_A_VENDOR_SITE = {
    "glp1forum.com", "peptidesource.net", "reddit.com", "t.me", "telegram.me",
    "discord.gg", "discord.com", "peptidebase.io", "thepeptidelist.com",
    "facebook.com", "instagram.com", "x.com", "twitter.com", "linkedin.com",
    "youtube.com", "tiktok.com", "wa.me", "finnrick.com",
}


@dataclass
class FinnrickContacts:
    slug: str
    name: str
    website: Optional[str] = None          # best guess at the vendor's own site
    websites: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    whatsapp: List[str] = field(default_factory=list)
    telegram: List[str] = field(default_factory=list)
    signal: List[str] = field(default_factory=list)
    other: List[str] = field(default_factory=list)  # "kind: value" for anything else
    location: Optional[str] = None
    status: Optional[str] = None
    test_count: int = 0

    @property
    def found_anything(self) -> bool:
        return bool(self.website or self.emails or self.whatsapp or self.telegram or self.signal or self.other)


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def slug_from_profile_url(url: str) -> Optional[str]:
    m = PROFILE_RE.search(url or "")
    return m.group(1).lower() if m else None


def _host(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_vendor_site(url: str) -> bool:
    host = _host(url)
    return bool(host) and _registrable(host) not in NOT_A_VENDOR_SITE and host not in NOT_A_VENDOR_SITE


def parse_contacts(vendor: dict) -> FinnrickContacts:
    """Turn one vendor record (index entry or detail) into FinnrickContacts.

    Pure function -- no network -- so it can be unit-tested on fixtures.
    """
    out = FinnrickContacts(
        slug=vendor.get("slug") or "",
        name=vendor.get("name") or "",
        location=vendor.get("location") or None,
        status=vendor.get("status") or None,
        test_count=int(vendor.get("test_count") or 0),
    )

    entries = list(vendor.get("contacts") or [])
    # The index carries a single headline contact alongside (or instead of)
    # the full list; fold it in so a vendor with only that still resolves.
    if vendor.get("contact_url"):
        entries.append({
            "kind": vendor.get("contact_kind") or "website",
            "url": vendor.get("contact_url"),
            "value": vendor.get("contact_url"),
            "vouched": False,
        })

    vouched_site: Optional[str] = None
    for c in entries:
        kind = (c.get("kind") or "").lower()
        url = (c.get("url") or "").strip()
        value = (c.get("value") or "").strip()
        if kind == "website":
            site = url or value
            if not site or not is_vendor_site(site):
                continue
            if "://" not in site:
                site = f"https://{site}/"
            if site not in out.websites:
                out.websites.append(site)
            if c.get("vouched") and vouched_site is None:
                vouched_site = site
        elif kind == "email":
            email = (value or url.replace("mailto:", "")).lower()
            if email and "@" in email and email not in out.emails:
                out.emails.append(email)
        elif kind == "whatsapp":
            num = value or url
            if num and num not in out.whatsapp:
                out.whatsapp.append(num)
        elif kind == "telegram":
            handle = value or url
            if handle and handle not in out.telegram:
                out.telegram.append(handle)
        elif kind == "signal":
            handle = value or url
            if handle and handle not in out.signal:
                out.signal.append(handle)
        elif value or url:
            out.other.append(f"{kind or 'contact'}: {value or url}")

    out.website = vouched_site or (out.websites[0] if out.websites else None)
    return out


class FinnrickClient:
    """Reads the vendor index once per run (it is ~1 MB) and answers lookups
    by slug or by name from it, fetching a vendor's detail record only when
    the index entry carries no contacts."""

    def __init__(self, cache_file: Optional[Path] = None, cache_ttl_seconds: int = 24 * 3600):
        self.cache_file = cache_file if cache_file is not None else (config.DATA_DIR / "finnrick_vendors.json")
        self.cache_ttl_seconds = cache_ttl_seconds
        self._by_slug: Optional[Dict[str, dict]] = None
        self._by_name: Dict[str, str] = {}
        self._headers = {"User-Agent": config.SCRAPER_USER_AGENT, "Accept": "application/json"}

    # -- index ---------------------------------------------------------------

    def _load_index(self) -> Dict[str, dict]:
        if self._by_slug is not None:
            return self._by_slug
        payload = None
        if self.cache_file and self.cache_file.exists():
            age = time.time() - self.cache_file.stat().st_mtime
            if age < self.cache_ttl_seconds:
                try:
                    payload = json.loads(self.cache_file.read_text())
                except ValueError:
                    payload = None
        if payload is None:
            logger.info("Fetching Finnrick vendor index")
            resp = requests.get(INDEX_URL, headers=self._headers, timeout=60)
            resp.raise_for_status()
            payload = resp.json()
            if self.cache_file:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(json.dumps(payload))
        items = payload.get("items") or payload.get("vendors") or []
        self._by_slug = {v["slug"]: v for v in items if v.get("slug")}
        self._by_name = {normalize_name(v.get("name", "")): v["slug"] for v in items if v.get("slug")}
        logger.info("Finnrick index: %d vendors", len(self._by_slug))
        return self._by_slug

    def index_size(self) -> int:
        return len(self._load_index())

    # -- lookups -------------------------------------------------------------

    def find(self, slug: Optional[str] = None, name: Optional[str] = None) -> Optional[dict]:
        index = self._load_index()
        if slug and slug in index:
            return index[slug]
        if name:
            found = self._by_name.get(normalize_name(name))
            if found:
                return index[found]
        return None

    def detail(self, slug: str) -> Optional[dict]:
        resp = requests.get(VENDOR_URL.format(slug=slug), headers=self._headers, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return (resp.json() or {}).get("vendor")

    def lookup(self, slug: Optional[str] = None, name: Optional[str] = None) -> Optional[FinnrickContacts]:
        entry = self.find(slug=slug, name=name)
        if entry is None:
            return None
        contacts = parse_contacts(entry)
        # The index carries only the headline contact (usually the website);
        # email, WhatsApp and the rest live on the detail record.
        if not entry.get("contacts"):
            try:
                full = self.detail(entry["slug"])
            except requests.RequestException as exc:
                logger.debug("finnrick detail failed for %s: %s", entry["slug"], exc)
                full = None
            if full:
                detailed = parse_contacts(full)
                if detailed.found_anything:
                    contacts = detailed
                    if contacts.website is None and entry.get("contact_url"):
                        contacts = parse_contacts({**full, "contact_url": entry.get("contact_url"),
                                                   "contact_kind": entry.get("contact_kind")})
            time.sleep(0.3)
        return contacts
