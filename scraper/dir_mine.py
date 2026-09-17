"""Mine clinic directories for the businesses they list.

Searching city by city returns tens of leads. A directory lists thousands, and
publishes a sitemap naming every listing page, so one pass over a sitemap is
worth hundreds of searches. This walks those sitemaps, opens each listing, and
pulls out the clinic's own name and website. Output is a "Name|domain" file that
scraper/lead_hunt.py takes as input.

    python3 scraper/dir_mine.py --list
    python3 scraper/dir_mine.py glp1directory --limit 400 --out cands.txt
    python3 scraper/dir_mine.py all --limit 900 --out cands.txt

A cursor per directory is kept in scraper/.dir_cursor so a daily run continues
where the last one stopped instead of re-reading the same listings.
"""
import argparse, concurrent.futures as cf, json, re, ssl, sys, urllib.error, urllib.request, gzip
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lead_hunt

CURSOR = HERE / ".dir_cursor"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# Each entry: the sitemaps that name listing pages, and the URL fragment that
# marks a listing rather than a category or location page.
DIRECTORIES = {
    "semaglutidenearme": {
        "sitemaps": ["https://semaglutidenearme.org/wp-sitemap-posts-gd_place-1.xml",
                     "https://semaglutidenearme.org/wp-sitemap-posts-gd_place-2.xml"],
        "listing": "/places/",
    },
    "glp1directory": {
        "sitemaps": ["https://glp1directory.com/provider-sitemap.xml",
                     "https://glp1directory.com/provider-sitemap2.xml",
                     "https://glp1directory.com/provider-sitemap3.xml"],
        "listing": "/provider/",
    },
    "globalglp1": {
        "sitemaps": ["https://globalglp1.com/sitemap-clinics.xml"],
        "listing": "/clinic/",
    },
    "mypeptidematch": {
        "sitemaps": ["https://www.mypeptidematch.com/sitemap.xml"],
        "listing": "/clinic/",
    },
    "healingmaps": {
        "sitemaps": ["https://healingmaps.com/listing-sitemap.xml",
                     "https://healingmaps.com/listing-sitemap2.xml",
                     "https://healingmaps.com/listing-sitemap3.xml"],
        "listing": "/listing/",
    },
    "peptideclinicfinder": {
        "sitemaps": ["https://peptideclinicfinder.com/sitemap.xml"],
        "listing": "/clinics/",
    },
    "medspadirectorypro": {
        "sitemaps": ["https://medspadirectorypro.com/sitemap.xml"],
        "listing": "/spa/",
    },
    # Checked and not usable: peptidefinder.us clinic pages link only to
    # themselves, and thepeptidelist.com returns 403 on provider pages. Both have
    # large sitemaps, so they look tempting; they are not worth the fetches.
    # usmedspadirectory.com lists 611 spas and every single "website" it gives
    # ends in .example.com -- the whole directory is invented, so nothing from it
    # can be trusted. medspafind.com renders its US listings in JavaScript, so the
    # clinic's own site never appears in the HTML. medicalspalocator.com claims
    # 18,000 providers but answers 429 to everything, even its sitemap.
    # klinic.com looks like the biggest prize of all -- 663 sitemaps covering
    # wegovy, zepbound, saxenda and TRT in every state -- but it is a telehealth
    # service writing city pages about itself, not a directory: its city pages
    # carry no link to any clinic but its own. americanmedspa.org publishes
    # articles and its sponsors, not its member spas.
}

# Links on a listing page that are never the clinic's own site.
NOT_THE_CLINIC = re.compile(
    r"(^|\.)(healingmaps|semaglutidenearme|glp1directory|globalglp1|mypeptidematch|peptidefinder|"
    r"thepeptidelist|locatepeptides|peptideassociation|pathtopeptides|peptidesuppliermatch|"
    r"peptidescertified|facebook|instagram|twitter|x|linkedin|youtube|tiktok|pinterest|google|"
    r"gstatic|googleapis|gravatar|wp|w3|gmpg|schema|recaptcha|unpkg|jsdelivr|cloudflare|cloudfront|"
    r"bootstrapcdn|fontawesome|jquery|revoffers|doubleclick|googletagmanager|wixstatic|shopify|"
    r"squarespace|yelp|zocdoc|healthgrades|vagaro|booksy|groupon|mapquest|apple|bing|yahoo|amazon|"
    # ad, analytics and consent networks, which appear on every listing page
    r"mediavine|taboola|outbrain|adsystem|adservice|scorecardresearch|quantserve|hotjar|segment|"
    r"onetrust|cookielaw|cookiedatabase|clarity|hubspot|intercom|calendly|linktr|bit|goo|tinyurl|"
    # widgets and reference sites carried by healingmaps listings
    r"vimeo|recaptcha|npiregistry|hhs|maps|nih|who|supabase|builder|example)\.", re.I)
# anchors a directory uses for the business's own site
WEBSITE_ANCHOR = re.compile(r">\s*(visit\s*(the\s*)?(website|site)|website|official\s*site|"
                            r"go\s*to\s*website|book|visit)\s*<", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def fetch(url, cap=600_000, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        b = r.read(cap)
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    return b.decode("utf-8", "replace")


def listing_urls(name):
    cfg = DIRECTORIES[name]
    out = []
    for sm in cfg["sitemaps"]:
        try:
            t = fetch(sm, cap=5_000_000, timeout=30)
        except Exception as e:
            print(f"  sitemap failed {sm}: {type(e).__name__}")
            continue
        out += [u for u in re.findall(r"<loc>([^<]+)</loc>", t)
                if cfg["listing"] in u and not u.rstrip("/").endswith(cfg["listing"].strip("/"))]
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def host_of(url):
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower().split(":")[0]


def clinic_of(url):
    """(name, domain) for one listing page, or None when it names no website.

    Picking the first external link is not enough: listing pages carry ad and
    analytics domains, and several directories link to themselves. So the
    directory's own host is excluded, known networks are excluded, and a link the
    page labels "Visit website" wins over a bare one.
    """
    try:
        h = fetch(url)
    except Exception:
        return None
    m = TITLE_RE.search(h)
    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""
    # directory titles read "Clinic Name - Directory" or "Clinic Name: Profile in City"
    name = re.split(r"\s+[-|–—:]\s+|\s+\|\s+", title)[0][:70] if title else ""
    self_host = host_of(url)
    self_base = self_host.split(".")[0]

    def usable(d):
        if "." not in d or d.endswith((".png", ".jpg", ".css", ".js", ".svg")):
            return False
        if d == self_host or d.endswith("." + self_host) or self_base in d:
            return False
        return not NOT_THE_CLINIC.search(d + ".")

    # a link the page labels as the business's website beats any other
    for mm in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.{0,80}?)</a>', h, re.S | re.I):
        href, inner = mm.group(1), mm.group(2)
        if WEBSITE_ANCHOR.search(">" + inner + "<") and usable(host_of(href)):
            return (name or host_of(href), host_of(href))
    for href in re.findall(r'href="(https?://[^"]+)"', h):
        d = host_of(href)
        if usable(d):
            return (name or d, d)
    return None


def cursors():
    try:
        return json.loads(CURSOR.read_text())
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory", nargs="?", default="all")
    ap.add_argument("--limit", type=int, default=400, help="listing pages to open this run")
    ap.add_argument("--out", default="candidates.txt")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for n in DIRECTORIES:
            print(f"{n:<22} {len(listing_urls(n)):>5} listings in sitemap")
        return

    names = list(DIRECTORIES) if a.directory == "all" else [a.directory]
    cur = cursors()
    known_bases, _ = lead_hunt.known()
    rows, opened = [], 0
    per = max(1, a.limit // len(names))

    for n in names:
        urls = listing_urls(n)
        start = cur.get(n, 0)
        batch = urls[start:start + per]
        print(f"{n}: {len(urls)} listings, taking {len(batch)} from offset {start}")
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            for got in ex.map(clinic_of, batch):
                opened += 1
                if got:
                    rows.append(got)
        cur[n] = start + len(batch)

    CURSOR.write_text(json.dumps(cur, indent=1))
    seen, keep, dupes = set(), [], 0
    for name, dom in rows:
        if dom in seen:
            continue
        seen.add(dom)
        b = lead_hunt.ss.brand_key(dom)
        if b and b in known_bases:
            dupes += 1
            continue
        keep.append(f"{name}|{dom}")
    Path(a.out).write_text("\n".join(keep), encoding="utf-8")
    print(f"\nopened {opened} listings -> {len(rows)} with a website -> {len(keep)} new "
          f"({dupes} already in the campaign)")
    print(f"wrote {a.out}")
    print(f"next: python3 scraper/lead_hunt.py {a.out} hunt.csv --workers=16")


if __name__ == "__main__":
    main()
