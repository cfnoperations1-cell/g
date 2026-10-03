"""Turn a list of candidate sites into qualified outreach leads.

Standard library only, so it runs under plain python3 after any container
restart. Search results come in from outside (a file of "Name|url" lines);
this crawls each site, decides whether it is a med spa or an RUO vendor, and
writes rows in the schema outreach/ingest_resolved.py already accepts.

    python3 scraper/lead_hunt.py candidates.txt out.csv [--workers 12]

A candidate is dropped here, before it ever reaches the queue, when it is an
aggregator or marketplace, when we already know the business, when the site
is plainly not US, or when no contact address is published.
"""
import concurrent.futures as cf
import csv, html as htmllib, re, socket, ssl, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "outreach"))
import serve_send as ss

UA = "Mozilla/5.0 (compatible; MarinexisLeadBot/1.0; +https://marinexisbiologics.com)"
TIMEOUT = 12
PATHS = ["", "/contact", "/contact-us", "/contact-us/", "/pages/contact", "/about",
         "/about-us", "/services", "/peptides", "/peptide-therapy", "/faq"]

# directories, marketplaces, review sites and platforms: never the business itself
AGGREGATOR = re.compile(
    r"(^|\.)(yelp|groupon|healthgrades|zocdoc|vagaro|booksy|realself|spafinder|mapquest|yellowpages|"
    r"facebook|instagram|twitter|x|linkedin|tiktok|youtube|pinterest|reddit|quora|amazon|ebay|etsy|"
    r"walmart|alibaba|aliexpress|made-in-china|indiamart|tradewheel|google|bing|duckduckgo|startpage|"
    r"wikipedia|webmd|healthline|drugs|medicalnewstoday|nih|ncbi|fda|wordpress|wixsite|squarespace|"
    # research-peptide queries pull in the literature; journals are not prospects
    r"medrxiv|biorxiv|pubmed|clinicaltrials|researchgate|sciencedirect|springer|wiley|mdpi|"
    r"frontiersin|jamanetwork|nejm|thelancet|cochrane|semanticscholar|scholar|arxiv|oup|"
    r"karger|tandfonline|sagepub|cell|nature|science|bmj|plos|hindawi|dovepress|"
    r"shopify|godaddysites|weebly|blogspot|medium|substack|glassdoor|indeed|crunchbase|bbb|"
    r"peptidebase|finnrick|thepeptidelist|peptiprices|trustpilot|sitejabber)\.", re.I)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL = re.compile(r"(^|@)(no-?reply|donotreply|postmaster|abuse|webmaster|example|sentry|wixpress|"
                       r"squarespace|godaddy|shopify|cloudflare|domain|privacy|dmca|sentry\.io)", re.I)
# Placeholder addresses left in a theme or a form mock-up. renumedispa.com
# published johndoe@gmail.com, which would have been emailed as a real contact.
PLACEHOLDER = re.compile(r"^(john|jane)\.?doe@|^(your|my)(name|email|address)@|^(name|email|username|user|"
                         r"firstname|lastname|sample|test|demo|dummy|placeholder|someone|somebody|"
                         r"changeme|address)@|@(example|test|domain|yourdomain|yoursite|mysite|"
                         r"emailaddress)\.", re.I)
BAD_END = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|woff2?|ico)$", re.I)
# Real top-level domains we accept. uschemlabs.com published "ugb@xq.gabwxp",
# a mangled string that looked like an address to a regex; a TLD check throws it
# out. Anything outside this list is treated as not an address.
KNOWN_TLD = {
    "com", "net", "org", "co", "io", "us", "biz", "info", "me", "health", "clinic", "care",
    "life", "app", "dev", "shop", "store", "site", "online", "xyz", "club", "email", "med",
    "medical", "spa", "beauty", "fit", "pro", "llc", "inc", "us.com", "gov", "edu", "vegas",
}


def cf_decode(hex_str):
    """Undo Cloudflare's email obfuscation.

    Cloudflare rewrites a published address into data-cfemail="<hex>", where the
    first byte is an XOR key. A browser decodes it and shows the address, so a
    crawler that ignores it reports "no email" for a site that does publish one.
    This is the single biggest reason clinics looked unreachable.
    """
    try:
        b = bytes.fromhex(hex_str)
        key = b[0]
        return "".join(chr(c ^ key) for c in b[1:])
    except Exception:
        return ""


CFEMAIL_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')

PHONE_RE = re.compile(r"\(?\b([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})\b")
STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
          "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
# the state has to sit in front of a ZIP; a bare ", MD" is a doctor's credential
STATE_ZIP_RE = re.compile(r",?\s*\b(" + "|".join(STATES) + r")\b[\s,]+(\d{5})(?:-\d{4})?\b")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)

MEDSPA_HINT = re.compile(r"med ?spa|medical spa|aesthetic|wellness clinic|our clinic|book (an )?appointment|"
                         r"schedule a consult|patients|hormone therapy|weight loss clinic|iv therapy|botox|"
                         r"filler|our providers|telehealth", re.I)
VENDOR_HINT = re.compile(r"research use only|not for human consumption|for research purposes|add to cart|"
                         r"wholesale|bulk pricing|coa|third.party tested|lyophilized|research peptides", re.I)
COLS = ["vendor", "domain", "email", "all_emails", "us_signal", "status", "evidence_url",
        "audience", "peptides", "peptide_hits"]


def terms(name):
    f = ROOT / "scraper" / name
    if not f.exists():
        return []
    return [t.strip().lower() for t in f.read_text(encoding="utf-8").splitlines()
            if t.strip() and not t.startswith("#")]


# What we can truthfully say we supply, and what merely tells us a site is worth
# emailing. A clinic advertising Ozempic is a prospect, but Ozempic is Novo
# Nordisk's product: what we supply is semaglutide, the compound in it. So brands
# find the lead and the generic is what gets named in the email.
QUOTABLE = terms("peptide_keywords.txt")
DISCOVERY = terms("discovery_terms.txt")
BRAND_TO_GENERIC = {
    "ozempic": "semaglutide", "wegovy": "semaglutide", "rybelsus": "semaglutide",
    "mounjaro": "tirzepatide", "zepbound": "tirzepatide",
    "saxenda": "liraglutide", "victoza": "liraglutide",
    "trulicity": "dulaglutide", "byetta": "exenatide",
}
# Said of a site that advertises the category but names no compound. True of us:
# the GLP-1 weight-loss drugs we supply are peptides.
GLP1_CATEGORY = ("glp-1", "glp1", "glp-2", "gip", "dual agonist", "triple agonist",
                 "incretin", "weight loss injection", "weight loss injections",
                 "weight loss shot", "medical weight loss", "skinny shot")
GLP1_FALLBACK = "GLP-1 weight loss peptides"


def known():
    bases, emails = set(), set()
    for name in ("outreach/sent_log.csv", "outreach/not_yet_emailed.csv"):
        p = ROOT / name
        if not p.exists():
            continue
        for r in csv.DictReader(open(p, newline="", encoding="utf-8")):
            e = (r.get("email") or "").strip().lower()
            if e:
                emails.add(e)
                bases.add(ss.brand_key(e.split("@", 1)[1]))
            d = (r.get("domain") or "").strip().lower()
            if d:
                bases.add(ss.brand_key(d))
    for r in csv.DictReader(open(ROOT / "outreach/draft_queue.csv", newline="", encoding="utf-8")):
        e = (r.get("email") or "").strip().lower()
        if e:
            emails.add(e)
            bases.add(ss.brand_key(e.split("@", 1)[1]))
    bases.discard("")
    return bases, emails


def domain_of(url):
    u = url.strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    host = urllib.parse.urlparse(u).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # many small practice sites have broken chains
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        return r.read(500_000).decode("utf-8", "replace")


def text_of(h):
    return re.sub(r"<[^>]+>", " ", TAG_RE.sub(" ", h))


def plausible(email, domain):
    """Is this address one this business could actually own?

    Accept it on the site's own domain or a subdomain, on a brand-matching
    domain, or at a real freemail provider. Anything else on the page belongs to
    somebody else -- a partner, a platform, or a decoding accident.
    """
    try:
        dom = email.split("@", 1)[1]
    except IndexError:
        return False
    if dom.rsplit(".", 1)[-1] not in KNOWN_TLD:
        return False
    if dom == domain or dom.endswith("." + domain) or domain.endswith("." + dom):
        return True
    if dom in ss.FREEMAIL:
        return True
    return bool(ss.brand_key(dom)) and ss.brand_key(dom) == ss.brand_key(domain)


def harvest(page):
    """Every address on a page, including the ones it tries not to show."""
    out = EMAIL_RE.findall(page)
    out += EMAIL_RE.findall(htmllib.unescape(page))      # &#64; style entities
    for hx in CFEMAIL_RE.findall(page):
        got = cf_decode(hx)
        if got and EMAIL_RE.fullmatch(got):
            out.append(got)
    return out


def pick_email(found, domain):
    clean, seen = [], set()
    for e in found:
        e = e.strip().strip(".").lower()
        if (BAD_EMAIL.search(e) or PLACEHOLDER.match(e) or BAD_END.search(e)
                or len(e) > 70 or e in seen or not plausible(e, domain)):
            continue
        seen.add(e)
        clean.append(e)
    if not clean:
        return "", []
    base = domain.split(".")[0]
    on_dom = [e for e in clean if e.split("@", 1)[1] == domain or base in e.split("@", 1)[1]]
    pool = on_dom or clean
    for pref in ("info@", "hello@", "contact@", "support@", "sales@", "office@",
                 "frontdesk@", "admin@", "orders@", "team@", "care@", "cs@"):
        for e in pool:
            if e.startswith(pref):
                return e, clean
    return pool[0], clean


SERVICE_LINK = re.compile(r"peptide|semaglutide|tirzepatide|glp-?1|weight.?loss|hormone|"
                          r"service|treatment|therap|menu|price", re.I)


def service_links(html_text, dom):
    """In-site links whose own URL says they describe what the business offers."""
    out = []
    for href in re.findall(r'href="([^"#]+)"', html_text):
        if not SERVICE_LINK.search(href):
            continue
        if href.startswith("/"):
            u = f"https://{dom}{href}"
        elif href.startswith(f"https://{dom}") or href.startswith(f"http://{dom}"):
            u = href
        elif href.startswith(f"https://www.{dom}"):
            u = href
        else:
            continue
        if u not in out:
            out.append(u)
    return out


def crawl(cand):
    name_hint, url = cand
    dom = domain_of(url)
    row = {c: "" for c in COLS}
    row["domain"] = dom
    row["vendor"] = name_hint
    if not dom:
        row["status"] = "not_found"
        return row
    emails, blob, title, ok, seen_url, extra = [], "", "", False, "", []
    for p in PATHS:
        try:
            h = fetch(f"https://{dom}{p}")
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
                ssl.SSLError, ValueError, OSError):
            continue
        except Exception:
            continue
        ok = True
        seen_url = seen_url or f"https://{dom}{p}"
        if not title:
            m = TITLE_RE.search(h)
            if m:
                title = re.sub(r"\s+", " ", text_of(m.group(1))).strip()[:120]
        emails += harvest(h)
        blob += " " + text_of(h).lower()
        if p == "":
            extra += service_links(h, dom)
        if emails and len(blob) > 25_000:
            break
    # A clinic's peptide menu is rarely at /peptides: renumedispa.com keeps it at
    # /wellness/peptide-therapy/. Without following the site's own links, real
    # peptide clinics score zero keyword hits and get dropped as irrelevant.
    for u in extra[:4]:
        try:
            h = fetch(u)
        except Exception:
            continue
        emails += harvest(h)
        blob += " " + text_of(h).lower()
    if not ok:
        row["status"] = "unreachable"
        return row
    row["vendor"] = name_hint or title or dom
    if title:
        row["evidence_url"] = seen_url
    em, allm = pick_email(emails, dom)
    row["email"] = em
    row["all_emails"] = "; ".join(allm[:8])
    quoted = [t for t in QUOTABLE if t in blob]
    found_disc = [t for t in DISCOVERY if t in blob]
    # a brand on the page means the compound inside it
    for b, generic in BRAND_TO_GENERIC.items():
        if b in blob and generic not in quoted:
            quoted.append(generic)
    if not quoted and any(c in blob for c in GLP1_CATEGORY):
        quoted.append(GLP1_FALLBACK)
    quoted = list(dict.fromkeys(quoted))          # keep first-seen order, drop repeats
    row["peptides"] = ", ".join(quoted[:6])
    row["peptide_hits"] = str(len(quoted) + len(found_disc))
    st = STATE_ZIP_RE.search(blob.upper())
    row["us_signal"] = "yes" if (st or PHONE_RE.search(blob)) else ""
    med, ven = len(MEDSPA_HINT.findall(blob)), len(VENDOR_HINT.findall(blob))
    row["audience"] = "medspa" if med > ven else "vendor"
    row["status"] = "ok" if em else "no_email"
    return row


def main(inp, outp, workers=12):
    bases, emails = known()
    cands, skipped = [], {"aggregator": 0, "already known": 0, "foreign": 0, "no domain": 0}
    seen = set()
    for line in Path(inp).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, url = (line.split("|", 1) + [""])[:2] if "|" in line else ("", line)
        name, url = name.strip(), (url or line).strip()
        dom = domain_of(url)
        if not dom:
            skipped["no domain"] += 1; continue
        if AGGREGATOR.search(dom + "."):
            skipped["aggregator"] += 1; continue
        if dom in seen:
            continue
        seen.add(dom)
        b = ss.brand_key(dom)
        if (b and b in bases) or dom in ss.FOREIGN_DOMAINS:
            skipped["already known"] += 1 if b in bases else 0
            if dom in ss.FOREIGN_DOMAINS:
                skipped["foreign"] += 1
            continue
        if ss.is_foreign(dom, name):
            skipped["foreign"] += 1; continue
        cands.append((name, url))

    print(f"{len(cands)} sites to crawl  (skipped: "
          + ", ".join(f"{k} {v}" for k, v in skipped.items() if v) + ")")
    rows = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, row in enumerate(ex.map(crawl, cands), 1):
            rows.append(row)
            if i % 25 == 0:
                print(f"  ...{i}/{len(cands)}")
    # a lead must have an address, mention something we sell, and look US-based
    for r in rows:
        if r["status"] == "ok":
            if int(r["peptide_hits"] or 0) < 1:
                r["status"] = "no_peptides"
            elif not r["us_signal"]:
                r["status"] = "no_us_signal"
            elif r["email"].lower() in emails:
                r["status"] = "already known"
    with open(outp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x["status"] != "ok", x["domain"])):
            w.writerow(r)
    from collections import Counter
    print("results:", dict(Counter(r["status"] for r in rows)))
    print(f"wrote {outp}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    w = 12
    for a in sys.argv[1:]:
        if a.startswith("--workers"):
            w = int(a.split("=", 1)[1]) if "=" in a else w
    if len(args) < 2:
        sys.exit(__doc__)
    main(args[0], args[1], w)
