"""Resolve vendor NAMES with no known website by trying name-derived domains and verifying the site
mentions both the vendor name and peptides. No search API needed."""
import re, sys, json, os, time
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup
S = os.environ.get("VENDOR_CRAWL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "vendor_crawl")); os.makedirs(S, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
names = [l.strip() for l in open(sys.argv[1] if len(sys.argv) > 1 else f"{S}/unresolved_names.txt") if l.strip()]
known = {l.split("\t")[0] for l in open(f"{S}/domains.tsv") if l.strip()}
known |= {l.split("\t")[2].strip() for l in open(os.path.join(ROOT, "scraper", "vendor_domains.tsv")) if not l.startswith("#") and l.count("\t") >= 2}
resolved_names = {l.split("\t")[0].lower() for l in open(os.path.join(ROOT, "scraper", "vendor_domains.tsv")) if not l.startswith("#") and l.strip()}
names = [n for n in dict.fromkeys(names) if n.lower() not in resolved_names]

def slugs(name):
    base = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("&", "and")).strip()
    words = [w for w in base.split() if w not in ("the", "inc", "co", "llc", "ltd", "us", "usa")]
    joined = "".join(words); dashed = "-".join(words)
    cands = [joined, dashed]
    if "peptide" in joined and not joined.endswith("s"): cands.append(joined + "s")
    if joined.endswith("s"): cands.append(joined[:-1])
    if len(words) > 1: cands.append("".join(w[0] for w in words[:-1]) + words[-1])
    out = []
    for c in dict.fromkeys(cands):
        for tld in (".com", ".co", ".net", ".io", ".us", ".shop", ".store"):
            out.append(c + tld)
    return out

def check(name):
    key = [w for w in re.sub(r"[^a-z0-9 ]", "", name.lower()).split() if len(w) > 2 and w not in ("peptide", "peptides", "labs", "lab", "research", "the")]
    for dom in slugs(name):
        if dom in known: continue
        try:
            r = requests.get(f"https://{dom}/", headers=H, timeout=12, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.status_code >= 400 and r.status_code not in (403, 503): continue
        t = r.text.lower()
        title = ""
        try: title = (BeautifulSoup(r.text, "lxml").title or BeautifulSoup("", "lxml")).get_text(" ", strip=True).lower()
        except Exception: pass
        final = r.url
        if r.status_code in (403, 503):
            return (name, dom, "blocked_unverified", final)
        namehit = all(k in t for k in key) if key else name.lower().replace(" ", "") in t.replace(" ", "")
        if "peptide" in t and (namehit or name.lower() in title):
            return (name, dom, "verified", final)
    return (name, None, "not_found", "")

with ThreadPoolExecutor(8) as ex:
    res = list(ex.map(check, names))
with open(sys.argv[2] if len(sys.argv) > 2 else f"{S}/guessed.tsv", "w") as f:
    for name, dom, st, final in res:
        f.write(f"{name}\t{dom or ''}\t{st}\t{final}\n")
        print(f"{name:35s} {st:20s} {dom or ''}")
print("GUESS_DONE", sum(1 for r in res if r[2] == "verified"), "verified of", len(names))
