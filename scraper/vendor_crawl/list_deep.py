"""Deeper harvest of coupon / list / directory pages already found: follow their internal peptide-related links (1 level) and collect outbound vendor domains."""
import os, re, sys, glob, time, json
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
ROOT=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","..")); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT,"peptide-lead-bot"))
import crawl
from bot.discover import domain_of, _skip, SKIP_DOMAINS
S = crawl.S
SKIP = set(SKIP_DOMAINS) | {"finnrick.com","peptidebase.io","thepeptidelist.com","startpage.com"}
seeds = set()
for f in glob.glob(f"{S}/listpages*.tsv"):
    for l in open(f): seeds.add(l.split("\t")[0].strip())
# also any cached search result whose URL path looks like a list/coupon/directory page
for f in glob.glob(f"{S}/search_cache*.json"):
    for q, items in json.load(open(f)).items():
        for x in items:
            u = x.get("href", "")
            if re.search(r"coupon|promo|discount|vendor|best-|top-|list|directory|review", u, re.I) and not _skip(domain_of(u)): seeds.add(u)
print(f"{len(seeds)} seed list pages", flush=True)
VEND = re.compile(r"peptide|pep|amino|research|lab|bio|chem|sarm|compound|synth|tide", re.I)
have = {l.split("\t")[0] for l in open(f"{S}/domains.tsv") if l.strip()}
found, visited = {}, set()
def harvest(u, depth):
    if u in visited or len(visited) > 1500: return
    visited.add(u)
    h, st = crawl.fetch(u)
    if not h: return
    soup = BeautifulSoup(h, "lxml"); base = domain_of(u); internal = []
    for a in soup.find_all("a", href=True):
        href = urljoin(u, a["href"]).split("#")[0]; d = domain_of(href)
        if not d or d in SKIP or _skip(d): continue
        if d == base:
            if depth < 1 and re.search(r"peptide|coupon|vendor|discount|promo|review|brand|store", href, re.I): internal.append(href)
        elif d not in have and d not in found and (VEND.search(d) or VEND.search(a.get_text(" ", strip=True)[:60])):
            found[d] = (u, a.get_text(" ", strip=True)[:60])
    time.sleep(0.8)
    for x in internal[:25]: harvest(x, depth + 1)
for i, u in enumerate(sorted(seeds), 1):
    try: harvest(u, 0)
    except Exception as e: print(f"  {u[:60]} {e}", flush=True)
    if i % 25 == 0: print(f"  {i}/{len(seeds)} seeds, {len(found)} new domains", flush=True)
with open(f"{S}/domains_list3.tsv", "w") as f, open(f"{S}/domains.tsv", "a") as g:
    for d, (via, anchor) in found.items():
        line = f"{d}\tlistdeep:{domain_of(via)}\t{anchor}\n"; f.write(line); g.write(line)
print(f"LISTDEEP_DONE new_domains={len(found)}", flush=True)
