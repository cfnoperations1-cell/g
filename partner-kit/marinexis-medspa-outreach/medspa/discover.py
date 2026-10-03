"""Find candidate med spa websites and record them for enrichment.

Standard library only. Works two ways, so an API key is optional:

  WITH a search API key in .env (SERPER_API_KEY, BRAVE_SEARCH_API_KEY, or
  GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX):

      python3 medspa/discover.py plan 12              # next 12 queries to run
      python3 medspa/discover.py search "medical spa peptide therapy Austin, TX"
      python3 medspa/discover.py run 12              # plan + search, all in one

  WITHOUT any key -- let Claude run the searches with its own web search tool
  and hand the result URLs back:

      python3 medspa/discover.py plan 12
      ... Claude searches each query ...
      python3 medspa/discover.py add "Glow Med Spa|https://glowmedspa.com/" "https://another.com/"

Everything lands in medspa/candidates.csv (deduped by domain). Nothing here
sends email or decides who to contact; enrich.py and build_queue.py do that.
"""
import csv, json, os, re, sys, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "medspa"
CAND = HERE / "candidates.csv"
CITIES = HERE / "cities.txt"
PROGRESS = HERE / ".query_progress"
COLS = ["domain", "business_name", "url", "city", "state", "query", "found_at"]
UA = os.environ.get("SCRAPER_USER_AGENT", "MarinexisLeadBot/1.0 (+contact: see .env SENDER_EMAIL)")
TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15"))

# Directories, marketplaces, publishers and franchises: real sites, but not a
# practice we can sell to. Anything here is dropped before it reaches the queue.
AGGREGATORS = {
    "yelp.com", "realself.com", "groupon.com", "vagaro.com", "booksy.com", "tripadvisor.com", "thumbtack.com",
    "mapquest.com", "yellowpages.com", "opencare.com", "zocdoc.com", "healthgrades.com", "spafinder.com",
    "classpass.com", "fresha.com", "google.com", "bing.com", "duckduckgo.com", "facebook.com", "instagram.com",
    "tiktok.com", "youtube.com", "x.com", "twitter.com", "pinterest.com", "nextdoor.com", "linkedin.com",
    "indeed.com", "glassdoor.com", "ziprecruiter.com", "reddit.com", "quora.com", "wikipedia.org", "amazon.com",
    "webmd.com", "healthline.com", "verywellhealth.com", "medicalnewstoday.com", "clinicaltrials.gov",
    "forbes.com", "nytimes.com", "cbsnews.com", "foxnews.com", "allure.com", "newbeauty.com", "byrdie.com",
    "hims.com", "ro.co", "joinmochi.com", "calibrate.com", "sesamecare.com", "wellness.com", "eventbrite.com",
    "squarespace.com", "wix.com", "wordpress.com", "godaddy.com", "shopify.com", "yahoo.com", "msn.com",
    "apple.com", "medium.com", "substack.com", "issuu.com", "scribd.com", "pdf.co",
}
SKIP_SUFFIX = (".gov", ".edu", ".mil")

QUERY_TEMPLATES = [
    'medical spa peptide therapy "{city}, {state}"',
    '"med spa" semaglutide OR tirzepatide "{city}, {state}"',
    'wellness clinic peptide injections "{city}, {state}"',
    'aesthetics clinic "BPC-157" OR "ipamorelin" "{city}, {state}"',
    '"{city}" {state} medspa hormone optimization peptides contact',
]


# ---------- candidate store ---------------------------------------------------
def load_candidates():
    if not CAND.exists():
        return {}
    with open(CAND, newline="", encoding="utf-8") as f:
        return {r["domain"]: r for r in csv.DictReader(f) if r.get("domain")}


def save_candidates(by_domain):
    HERE.mkdir(parents=True, exist_ok=True)
    with open(CAND, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for d in sorted(by_domain):
            w.writerow({k: by_domain[d].get(k, "") for k in COLS})


def domain_of(url):
    d = urllib.parse.urlparse(url if "//" in url else "//" + url).netloc.lower().split(":")[0]
    return d[4:] if d.startswith("www.") else d


def usable(domain):
    if not domain or "." not in domain:
        return False
    if domain in AGGREGATORS or domain.endswith(SKIP_SUFFIX):
        return False
    return not any(domain.endswith("." + a) for a in AGGREGATORS)


def record(hits, query="", city="", state=""):
    """hits: iterable of (business_name, url). Returns how many were new."""
    from datetime import datetime, timezone
    by_domain = load_candidates()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    added = 0
    for name, url in hits:
        d = domain_of(url)
        if not usable(d) or d in by_domain:
            continue
        by_domain[d] = {"domain": d, "business_name": (name or "").strip()[:120], "url": url,
                        "city": city, "state": state, "query": query, "found_at": stamp}
        added += 1
    save_candidates(by_domain)
    return added, len(by_domain)


# ---------- env / providers ---------------------------------------------------
def env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, val = line.split("=", 1)
                if k.strip() == key:
                    return val.strip().strip('"').strip("'")
    return default


def _get(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def search_api(query):
    """Return [(title, url)] from whichever provider has a key. Empty if none configured."""
    if env("SERPER_API_KEY"):
        body = json.dumps({"q": query, "num": 20}).encode()
        j = _get("https://google.serper.dev/search", {"X-API-KEY": env("SERPER_API_KEY"),
                                                      "Content-Type": "application/json"}, body)
        return [(x.get("title", ""), x.get("link", "")) for x in j.get("organic", [])]
    if env("BRAVE_SEARCH_API_KEY"):
        q = urllib.parse.urlencode({"q": query, "count": 20})
        j = _get(f"https://api.search.brave.com/res/v1/web/search?{q}",
                 {"X-Subscription-Token": env("BRAVE_SEARCH_API_KEY"), "Accept": "application/json"})
        return [(x.get("title", ""), x.get("url", "")) for x in j.get("web", {}).get("results", [])]
    if env("GOOGLE_CSE_API_KEY") and env("GOOGLE_CSE_CX"):
        q = urllib.parse.urlencode({"key": env("GOOGLE_CSE_API_KEY"), "cx": env("GOOGLE_CSE_CX"),
                                    "q": query, "num": 10})
        j = _get(f"https://www.googleapis.com/customsearch/v1?{q}")
        return [(x.get("title", ""), x.get("link", "")) for x in j.get("items", [])]
    return []


# ---------- query planning ----------------------------------------------------
def cities():
    if not CITIES.exists():
        return []
    out = []
    for line in CITIES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "," not in line:
            continue
        city, state = [p.strip() for p in line.rsplit(",", 1)]
        out.append((city, state))
    return out


def plan(n):
    """Next n queries, walking cities x templates and remembering where we stopped."""
    pairs = cities()
    if not pairs:
        sys.exit("medspa/cities.txt is empty -- add 'City, ST' lines first.")
    start = int(PROGRESS.read_text().strip()) if PROGRESS.exists() else 0
    total = len(pairs) * len(QUERY_TEMPLATES)
    out = []
    for i in range(start, min(start + n, total)):
        city, state = pairs[i // len(QUERY_TEMPLATES)]
        tpl = QUERY_TEMPLATES[i % len(QUERY_TEMPLATES)]
        out.append((i, tpl.format(city=city, state=state), city, state))
    return out, total


def advance(to_index):
    PROGRESS.write_text(str(to_index))


# ---------- commands ----------------------------------------------------------
def cmd_plan(n):
    items, total = plan(n)
    if not items:
        print("plan: all queries used. Add more cities to medspa/cities.txt or reset medspa/.query_progress")
        return
    print(f"# {len(items)} queries (position {items[0][0]}..{items[-1][0]} of {total})")
    for _, q, city, state in items:
        print(q)


def cmd_search(query, city="", state=""):
    hits = search_api(query)
    if not hits:
        print("no search API key configured (SERPER_API_KEY / BRAVE_SEARCH_API_KEY / GOOGLE_CSE_*).")
        print("Use Claude's own web search for this query, then: python3 medspa/discover.py add \"Name|url\" ...")
        return
    added, total = record([(t, u) for t, u in hits if u], query, city, state)
    print(f"search: {len(hits)} results, {added} new candidates (total {total})")


def cmd_run(n):
    items, _ = plan(n)
    if not items:
        print("nothing to run")
        return
    if not search_api("test"):
        print("no search API key configured -- use `plan` + Claude web search + `add` instead.")
        return
    last = items[0][0]
    for idx, q, city, state in items:
        try:
            cmd_search(q, city, state)
            last = idx
        except Exception as e:
            print(f"! query failed ({type(e).__name__}: {e}) -- stopping at position {idx}")
            break
    advance(last + 1)
    print(f"progress -> {last + 1}")


def cmd_add(args):
    """Each arg is 'url' or 'Business Name|url'. City/state can lead: 'Austin|TX|Name|url'."""
    hits, city, state = [], "", ""
    for a in args:
        parts = a.split("|")
        if len(parts) >= 4:
            city, state, name, url = parts[0], parts[1], parts[2], parts[3]
        elif len(parts) == 2:
            name, url = parts
        else:
            name, url = "", parts[0]
        hits.append((name, url.strip()))
    added, total = record(hits, "claude_web_search", city, state)
    print(f"add: {len(hits)} urls, {added} new candidates (total {total})")


def cmd_stats():
    by = load_candidates()
    pos = int(PROGRESS.read_text().strip()) if PROGRESS.exists() else 0
    _, total = plan(1) if cities() else ([], 0)
    print(f"candidates={len(by)} query_position={pos}/{total}")
    from collections import Counter
    st = Counter(r.get("state", "") for r in by.values() if r.get("state"))
    if st:
        print("by state:", dict(st.most_common(10)))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "stats":
        cmd_stats()
    elif a[0] == "plan":
        cmd_plan(int(a[1]) if len(a) > 1 else 10)
    elif a[0] == "search":
        cmd_search(a[1], a[2] if len(a) > 2 else "", a[3] if len(a) > 3 else "")
    elif a[0] == "run":
        cmd_run(int(a[1]) if len(a) > 1 else 10)
    elif a[0] == "add":
        cmd_add(a[1:])
    else:
        sys.exit("usage: discover.py [stats | plan N | search QUERY [city] [ST] | run N | add 'Name|url' ...]")
