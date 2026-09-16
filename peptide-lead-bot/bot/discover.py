"""Discovery: turn search queries into candidate businesses (name, website, address...).

Providers, in priority order:
  1. Google Places API (New) - structured med spa / clinic data incl. phone + website
  2. Serper.dev - Google web results, great for vendors and directory pages
  3. DuckDuckGo (ddgs) - free fallback, no key needed
"""
import os
import re
import time
import requests
import tldextract

SKIP_DOMAINS = {
    "google.com", "yelp.com", "facebook.com", "instagram.com", "linkedin.com", "youtube.com",
    "tiktok.com", "reddit.com", "wikipedia.org", "amazon.com", "healthgrades.com", "zocdoc.com",
    "webmd.com", "mapquest.com", "yellowpages.com", "bbb.org", "groupon.com", "pinterest.com",
    "twitter.com", "x.com", "nih.gov", "fda.gov", "quora.com", "apple.com", "medium.com",
}
DELAY = float(os.getenv("REQUEST_DELAY_SECONDS", "1.5"))


def domain_of(url: str) -> str:
    if not url:
        return ""
    ext = tldextract.extract(url)
    if not ext.domain or not ext.suffix:
        return ""
    return f"{ext.domain}.{ext.suffix}".lower()


def _skip(dom: str) -> bool:
    return not dom or any(dom == s or dom.endswith("." + s) for s in SKIP_DOMAINS)


def _parse_city_state(addr: str):
    m = re.search(r",\s*([^,]+?),\s*([A-Z]{2})\s+(\d{5})", addr or "")
    if m:
        return m.group(1).strip(), m.group(2), m.group(3)
    return "", "", ""


# ---------------- Google Places (New) ----------------
def google_places(query: str, max_results=60):
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key:
        return []
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.websiteUri,"
                            "places.nationalPhoneNumber,places.rating,places.userRatingCount,"
                            "places.googleMapsUri,places.primaryTypeDisplayName,nextPageToken",
    }
    out, token = [], None
    while len(out) < max_results:
        body = {"textQuery": query, "pageSize": 20}
        if token:
            body["pageToken"] = token
        r = requests.post(url, json=body, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"    [places] HTTP {r.status_code}: {r.text[:200]}")
            break
        data = r.json()
        for p in data.get("places", []):
            addr = p.get("formattedAddress", "")
            city, state, zipc = _parse_city_state(addr)
            out.append({
                "business_name": p.get("displayName", {}).get("text", ""),
                "website": p.get("websiteUri", ""),
                "domain": domain_of(p.get("websiteUri", "")),
                "phones": p.get("nationalPhoneNumber", ""),
                "address": addr, "city": city, "state": state, "zip": zipc,
                "rating": p.get("rating", ""), "review_count": p.get("userRatingCount", ""),
                "google_maps_url": p.get("googleMapsUri", ""),
                "category": p.get("primaryTypeDisplayName", {}).get("text", ""),
                "source": "google_places", "source_query": query,
            })
        token = data.get("nextPageToken")
        if not token:
            break
        time.sleep(2)
    return [o for o in out if o["domain"] and not _skip(o["domain"])]


# ---------------- Serper.dev ----------------
def serper(query: str, pages=3):
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return []
    out = []
    for page in range(1, pages + 1):
        r = requests.post("https://google.serper.dev/search",
                          json={"q": query, "num": 10, "page": page, "gl": "us"},
                          headers={"X-API-KEY": key}, timeout=30)
        if r.status_code != 200:
            print(f"    [serper] HTTP {r.status_code}")
            break
        for item in r.json().get("organic", []):
            dom = domain_of(item.get("link", ""))
            if _skip(dom):
                continue
            out.append({"business_name": item.get("title", "").split(" | ")[0].split(" - ")[0].strip(),
                        "website": item.get("link", ""), "domain": dom,
                        "notes": item.get("snippet", ""), "source": "serper", "source_query": query})
        time.sleep(DELAY)
    return out


# ---------------- DuckDuckGo fallback ----------------
def duckduckgo(query: str, max_results=30):
    try:
        from ddgs import DDGS
    except ImportError:
        print("    [ddg] pip install ddgs")
        return []
    out = []
    try:
        with DDGS() as d:
            for item in d.text(query, max_results=max_results, region="us-en"):
                dom = domain_of(item.get("href", ""))
                if _skip(dom):
                    continue
                out.append({"business_name": item.get("title", "").split(" | ")[0].split(" - ")[0].strip(),
                            "website": item.get("href", ""), "domain": dom,
                            "notes": item.get("body", ""), "source": "duckduckgo", "source_query": query})
    except Exception as e:
        print(f"    [ddg] {e}")
    time.sleep(DELAY * 2)
    return out


def search(query: str, local: bool):
    """local=True (med spas/clinics): Places first. local=False (vendors): Serper first."""
    results = []
    if local and os.getenv("GOOGLE_PLACES_API_KEY"):
        results = google_places(query)
    if not results and os.getenv("SERPER_API_KEY"):
        results = serper(query)
    if not results:
        results = duckduckgo(query)
    return results
