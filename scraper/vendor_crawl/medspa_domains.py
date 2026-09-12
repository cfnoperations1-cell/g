"""Turn medspa search-cache results directly into a domains list (the result sites ARE the businesses)."""
import json, os, sys
from urllib.parse import urlparse
ROOT=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","..")); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT,"peptide-lead-bot"))
from bot.discover import _skip, SKIP_DOMAINS
S = os.environ.get("VENDOR_CRAWL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","..","data","vendor_crawl")); os.makedirs(S, exist_ok=True)
cache = json.load(open(f"{S}/medspa_cache.json")) if os.path.exists(f"{S}/medspa_cache.json") else {}
# aggregator / directory domains that are not themselves a med spa business
AGG = set(SKIP_DOMAINS) | {"yelp.com","realself.com","groupon.com","vagaro.com","booksy.com","tripadvisor.com","thumbtack.com",
    "mapquest.com","yellowpages.com","opencare.com","zocdoc.com","healthgrades.com","spafinder.com","classpass.com","fresha.com",
    "google.com","bing.com","facebook.com","instagram.com","tiktok.com","nextdoor.com"," زocdoc.com","weedmaps.com","allure.com",
    "newbeauty.com","byrdie.com","reddit.com","wikipedia.org","amazon.com","webmd.com","clinicaltrials.gov","gov"}
def dom(u):
    d=urlparse(u).netloc.lower().split(":")[0]
    return d[4:] if d.startswith("www.") else d
rows={}
for q, items in cache.items():
    city = q.split(" ")[-2] + " " + q.split(" ")[-1] if "," in q else ""
    for x in items:
        d=dom(x.get("href",""))
        if not d or _skip(d) or d in AGG or d.endswith(".gov"): continue
        import re as _re
        mm=_re.search(r",\s*([A-Z]{2})\b", q); stt=mm.group(1) if mm else ""
        cm=_re.search(r"([A-Za-z .]+),\s*[A-Z]{2}", q); cty=cm.group(1).strip() if cm else ""
        rows.setdefault(d, (x.get("title","")[:80], q, stt, cty))
have={l.split("\t")[0] for l in open(f"{S}/medspa/domains.tsv")} if os.path.exists(f"{S}/medspa/domains.tsv") else set()
with open(f"{S}/medspa/domains.tsv","w") as f:
    for d,(name,q,stt,cty) in rows.items():
        f.write(f"{d}\tmedspa_search|{stt}|{cty}\t{name}\n")
print("medspa candidate domains:", len(rows))
