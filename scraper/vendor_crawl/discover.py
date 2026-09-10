"""Discovery via the search library the bot already uses (ddgs, bing backend):
find coupon/affiliate pages, vendor directories and 'best vendor' lists, then harvest the
vendor sites they link out to. Writes domains_disc.tsv + listpages.tsv."""
import os, re, sys, time, json
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl
from bot.discover import domain_of, _skip, SKIP_DOMAINS
S = crawl.S
QUERIES = ["peptide discount code", "peptide coupon code 2026", "research peptides coupon code", "peptide vendor promo code", "peptide affiliate discount code",
           "best peptide vendors 2026", "best research peptide companies USA", "peptide vendor list", "peptide vendor directory", "peptide vendor reviews",
           "peptide vendor database third party tested", "trusted peptide vendors list", "top peptide suppliers USA", "USA made research peptides",
           "research peptides USA supplier", "buy research peptides online USA", "peptide vendors ship from USA", "peptide vendor comparison",
           "BPC-157 coupon code", "retatrutide vendor coupon", "tirzepatide research peptide vendor USA", "semaglutide research vendor coupon",
           "peptide store promo code", "wholesale peptides USA supplier", "peptides for sale USA lab tested", "peptide company discount code reddit",
           "peptide vendor coupon codes list", "peptide sciences alternatives", "verified peptide vendors COA", "peptide supplier reviews 2026",
           "core peptides coupon code", "amino asylum discount code", "limitless life nootropics coupon", "biotech peptides coupon", "swiss chems coupon code",
           "chemyo coupon code", "sports technology labs coupon", "pure rawz coupon code", "particle peptides discount", "peptide sciences coupon code",
           "research peptides promo code list", "peptide vendors coupon aggregator", "peptide brand discount codes influencer", "peptide vendor affiliate program",
           "peptide vendor tier list", "peptide vendor rankings tested", "peptide vendor scorecard", "janoshik tested peptide vendors list"]
SKIP = set(SKIP_DOMAINS) | {"finnrick.com", "peptidebase.io", "thepeptidelist.com", "tobaccovillenc.org", "stonevillenc.org"}
hits = {}   # domain -> {"via": query or list page, "kind": "result"|"outlink", "anchor": ...}
pages = {}  # result url -> query
CACHE = f"{S}/search_cache.json"
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
with DDGS(timeout=25) as d:
    for q in QUERIES:
        if q in cache:
            res = cache[q]
        else:
            res = None
            for attempt in range(3):
                try:
                    res = list(d.text(q, max_results=30, backend="bing")); break
                except Exception as e:
                    print(f"[{q}] attempt {attempt+1} failed: {str(e)[:80]}", flush=True); time.sleep(30 * (attempt + 1))
            if res is None: continue
            cache[q] = res; json.dump(cache, open(CACHE, "w")); time.sleep(8)
        n = 0
        for x in res:
            u = x.get("href", ""); dom = domain_of(u)
            if not dom or _skip(dom) or dom in SKIP: continue
            pages.setdefault(u, q); hits.setdefault(dom, {"via": q, "kind": "result", "anchor": x.get("title", "")[:80]}); n += 1
        print(f"[{q}] {n} usable results", flush=True)
print(f"{len(pages)} result pages, {len(hits)} domains; now harvesting outbound links from list-like pages", flush=True)
VENDORISH = re.compile(r"peptide|pep|amino|research|lab|bio|chem|sarm|compound|synth|tide", re.I)
listpages = []
for i, (u, q) in enumerate(list(pages.items())[:250]):
    dom = domain_of(u)
    try:
        h, st = crawl.fetch(u)
    except Exception:
        continue
    if not h: continue
    soup = BeautifulSoup(h, "lxml")
    out = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(u, a["href"]); od = domain_of(href)
        if od and od != dom and not _skip(od) and od not in SKIP and (VENDORISH.search(od) or VENDORISH.search(a.get_text(" ", strip=True)[:60])):
            out.setdefault(od, a.get_text(" ", strip=True)[:60])
    text = soup.get_text(" ", strip=True).lower()
    is_list = len(out) >= 5 and ("coupon" in text or "discount" in text or "vendor" in text or "best" in text or "review" in text)
    if is_list:
        listpages.append((u, q, len(out)))
        for od, anchor in out.items():
            hits.setdefault(od, {"via": f"list:{dom}", "kind": "outlink", "anchor": anchor})
    print(f"  ({i+1}) {dom[:40]:40s} outlinks={len(out):3d} list={is_list}", flush=True)
    time.sleep(1.0)
have = {l.split("\t")[0] for l in open(f"{S}/domains.tsv") if l.strip()}
with open(f"{S}/domains_disc.tsv", "w") as f, open(f"{S}/domains.tsv", "a") as g:
    n = 0
    for dom, m in hits.items():
        if dom in have: continue
        line = f"{dom}\tdiscovery:{m['kind']}:{m['via'][:60]}\t{m['anchor']}\n"; f.write(line); g.write(line); n += 1
with open(f"{S}/listpages.tsv", "w") as f:
    for u, q, k in listpages: f.write(f"{u}\t{q}\t{k}\n")
print(f"DISC_DONE new_domains={n} listpages={len(listpages)}", flush=True)
