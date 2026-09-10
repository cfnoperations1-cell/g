"""Polite concurrent crawler: visit each vendor domain's own public pages, pull emails/phones,
classify US presence + vendor type. Resumable: appends one JSON line per domain to results.jsonl."""
import json, os, re, sys, time, threading, html as htmlmod
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "peptide-lead-bot"))
from scraper.classify import guess_us_presence, sells_direct, manufactures, is_content_site, classify_company_type
from bot.keywords import PEPTIDE_TERMS

S = os.environ.get("VENDOR_CRAWL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "vendor_crawl")); os.makedirs(S, exist_ok=True)
IN, OUT = sys.argv[1] if len(sys.argv) > 1 else f"{S}/domains.tsv", os.environ.get("CRAWL_OUT", f"{S}/results.jsonl")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
DELAY, TIMEOUT, MAX_LINKED, MAX_FIXED, WORKERS = 1.0, 20, 6, 6, int(os.environ.get("CRAWL_WORKERS", "8"))
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
OBF_RE = re.compile(r"([a-zA-Z0-9._%+-]+)\s*[\[(]\s*at\s*[\])]\s*([a-zA-Z0-9.-]+)\s*[\[(]\s*dot\s*[\])]\s*([a-zA-Z]{2,})", re.I)
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
CF_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
RESEARCH_ONLY = ["for research use only", "research use only", "not for human consumption", "not intended for human", "research purposes only", "laboratory research use only"]
PRIORITY = ["contact", "about", "privacy", "terms", "support", "faq", "wholesale", "shipping", "refund", "return", "policies"]
FIXED = ["/contact", "/contact-us", "/pages/contact", "/pages/contact-us", "/policies/contact-information", "/about", "/about-us",
         "/pages/about-us", "/pages/about", "/privacy-policy", "/pages/privacy-policy", "/policies/privacy-policy", "/policies/terms-of-service",
         "/terms-of-service", "/terms-and-conditions", "/pages/faq", "/faq", "/support", "/pages/shipping-policy", "/shipping-policy"]
JUNK = ("example.", "sentry", "wixpress", "domain.com", "email.com", "yourdomain", "yoursite", "mysite.com", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
        "godaddy", "wordpress", "squarespace", "shopify.com", "company.com", "yourwebsite.com", "hostingersite.com", "yourcompany", "noreply", "no-reply", "donotreply", "@2x", "@3x", "schema.org", "w3.org", "test.com", "email@", "name@", "user@", "you@")
SKIP_TAGS = ["script", "style", "noscript", "svg"]
lock = threading.Lock()
_robots = {}

def robots_ok(dom, url):
    rp = _robots.get(dom)
    if rp is None:
        rp = RobotFileParser()
        try:
            r = requests.get(f"https://{dom}/robots.txt", headers=H, timeout=10)
            if r.status_code == 200 and "text/html" not in r.headers.get("content-type", ""):
                rp.parse(r.text.splitlines())
            else:
                rp.parse([])  # no usable robots.txt (4xx/5xx/HTML challenge) -> allow, per Google's convention
        except requests.RequestException:
            rp.parse([])
        _robots[dom] = rp
    try: return rp.can_fetch("*", url)
    except Exception: return True

def cf_decode(hexs):
    b = bytes.fromhex(hexs); k = b[0]
    return "".join(chr(c ^ k) for c in b[1:])

def fetch(url):
    """returns (html, status) status in ok|blocked|error"""
    try:
        r = requests.get(url, headers=H, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        return "", f"error:{type(e).__name__}"
    t = r.text or ""
    if r.status_code in (403, 429, 503) or "cf-browser-verification" in t or "<title>Just a moment" in t or "challenge-platform" in t:
        return "", f"blocked:{r.status_code}"
    if r.status_code != 200: return "", f"http:{r.status_code}"
    if "text/html" not in r.headers.get("content-type", "text/html"): return "", "nonhtml"
    return t, "ok"

def extract(html, base):
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    og = soup.find("meta", property="og:site_name"); site_name = og.get("content", "") if og else ""
    emails = set(EMAIL_RE.findall(html))
    for m in OBF_RE.findall(html): emails.add(f"{m[0]}@{m[1]}.{m[2]}")
    for h in CF_RE.findall(html):
        try: emails.add(cf_decode(h))
        except Exception: pass
    for a in soup.select("a[href^=mailto]"): emails.add(htmlmod.unescape(a["href"][7:]).split("?")[0])
    phones = set()
    for a in soup.select("a[href^=tel]"): phones.add(a["href"][4:])
    for t in soup(SKIP_TAGS): t.decompose()
    text = soup.get_text(" ", strip=True)
    phones |= set(PHONE_RE.findall(text))
    base_host = urlparse(base).netloc.lower().replace("www.", "")
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("#")[0]
        p = urlparse(href)
        if p.scheme in ("http", "https") and p.netloc.lower().replace("www.", "") == base_host and not re.search(r"\.(png|jpg|jpeg|gif|pdf|zip|css|js)$", p.path, re.I):
            links.append(href)
    return text, emails, phones, links, title, site_name

PLACEHOLDER_LOCAL = {"your", "you", "name", "email", "user", "username", "test", "someone", "john", "jane", "firstname", "example", "yourname", "myemail", "mail", "e-mail", "emailaddress", "address", "johndoe", "jdoe"}
def clean_emails(emails, dom):
    out = set()
    for e in emails:
        e = e.strip().strip(".").lower()
        e = re.sub(r"^(?:u003e|u003c|x3e|x3c|%3e|%3c|%20|3e|3d)+", "", e)
        if any(j in e for j in JUNK) or len(e) > 60 or e.count("@") != 1: continue
        local, host = e.split("@")
        if ".." in e or local.startswith((".", "-")) or local.endswith("."): continue
        if not local or local in PLACEHOLDER_LOCAL or "." not in host or len(host.split(".")[-1]) < 2: continue
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host) or re.search(r"\.[a-z]{2,}-", host): continue   # "gmail.com-testi.mp"
        if re.search(r"\.(com|net|org)\.(?!au$|uk$|br$|mx$|cn$|tw$|hk$)[a-z]{2,3}$", host): continue       # "ngpeptide.com.we"
        if re.search(r"\.(js|css|html|php|webp)$", e): continue
        if re.match(r"^[0-9a-f]{16,}$", local): continue
        out.add(e)
    ROLE = ["support", "info", "sales", "contact", "hello", "orders", "wholesale", "customerservice", "service", "help", "admin", "office", "team"]
    def rk(x):
        local, host = x.split("@")
        return (0 if host.endswith(dom) else 1, next((i for i, r in enumerate(ROLE) if local.startswith(r)), len(ROLE)), x)
    return sorted(out, key=rk)[:6]

def clean_phones(phones):
    out = set()
    for p in phones:
        d = re.sub(r"\D", "", p)
        if len(d) == 11 and d.startswith("1"): d = d[1:]
        if len(d) == 10 and not d.startswith(("0", "1")): out.add(f"({d[:3]}) {d[3:6]}-{d[6:]}")
    return sorted(out)

def crawl(dom, src, name):
    rec = {"domain": dom, "source": src, "roster_name": name, "pages_checked": [], "status": "", "emails": [], "phones": [], "email_page": ""}
    if not robots_ok(dom, f"https://{dom}/"):
        rec["status"] = "robots_disallowed"; return rec
    home, status = "", ""
    for start in (f"https://{dom}/", f"https://www.{dom}/", f"http://{dom}/"):
        home, status = fetch(start)
        if home: break
    rec["status"] = status
    if not home: return rec
    base = start
    texts, emails, phones = [], set(), set()
    t, e, p, links, title, site_name = extract(home, base)
    texts.append(t); emails |= e; phones |= p; rec["pages_checked"].append(base)
    rec["company_name"] = (site_name or title.split(" | ")[0].split(" – ")[0].split(" - ")[0]).strip()[:80]
    if clean_emails(e, dom): rec["email_page"] = base
    visited = {base.rstrip("/")}
    def rank(u):
        ul = u.lower(); return min((i for i, k in enumerate(PRIORITY) if k in ul), default=99)
    linked = [u for u in sorted(set(links), key=rank) if rank(u) < 99][:MAX_LINKED]
    fixed = [urljoin(base, f) for f in FIXED]
    n_fixed = 0
    for u in linked + fixed:
        if u.rstrip("/") in visited: continue
        is_fixed = u not in linked
        if is_fixed:
            if n_fixed >= MAX_FIXED or clean_emails(emails, dom): break
            n_fixed += 1
        visited.add(u.rstrip("/"))
        if not robots_ok(dom, u): continue
        time.sleep(DELAY)
        h, st = fetch(u)
        if not h: continue
        t, e, p, _, _, _ = extract(h, u)
        texts.append(t); emails |= e; phones |= p; rec["pages_checked"].append(u)
        if clean_emails(e, dom) and not rec["email_page"]: rec["email_page"] = u
    full = " ".join(texts); low = full.lower()
    rec["emails"] = clean_emails(emails, dom); rec["phones"] = clean_phones(phones)[:3]
    rec["peptide_terms"] = sorted({k for k in PEPTIDE_TERMS if k in low})
    us, state = guess_us_presence(full); rec["us_based"], rec["state"] = us, state
    ro = next((x for x in RESEARCH_ONLY if x in low), None)
    rec["research_only_evidence"] = ro
    rec["sells_direct"] = sells_direct(full); rec["manufactures"] = manufactures(full); rec["content_site"] = is_content_site(full)
    rec["company_type"] = classify_company_type(full, ro) if rec["peptide_terms"] else "not_peptide_related"
    rec["text_len"] = len(full)
    return rec

def main():
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT): done.add(json.loads(line)["domain"])
    rows = [l.rstrip("\n").split("\t") for l in open(IN) if l.strip()]
    todo = [(r[0], r[1] if len(r) > 1 else "", r[2] if len(r) > 2 else "") for r in rows if r[0] not in done]
    print(f"{len(todo)} domains to crawl ({len(done)} already done)", flush=True)
    def work(item):
        dom, src, name = item
        try: rec = crawl(dom, src, name)
        except Exception as ex: rec = {"domain": dom, "source": src, "roster_name": name, "status": f"exception:{type(ex).__name__}:{ex}"[:200], "emails": [], "pages_checked": []}
        with lock:
            with open(OUT, "a") as f: f.write(json.dumps(rec) + "\n")
            print(f"{dom:35s} {rec['status']:14s} pages={len(rec['pages_checked'])} emails={rec.get('emails')}", flush=True)
    with ThreadPoolExecutor(WORKERS) as ex: list(ex.map(work, todo))
    print("CRAWL_DONE", flush=True)

if __name__ == "__main__": main()
