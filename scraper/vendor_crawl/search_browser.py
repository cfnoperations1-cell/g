"""Fill search_cache.json (same shape discover.py expects) using headless Chromium on Bing."""
import os, sys, json, time, re
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl
S = crawl.S
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "discover.py")).read()
QUERIES = eval(src[src.index("QUERIES = ["):src.index("]", src.index('"janoshik tested peptide vendors list"'))+1].split("=",1)[1])
CACHE = f"{S}/search_cache.json"; cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
def unwrap(u):
    if "bing.com/ck/a" in u:
        m = re.search(r"[&?]u=a1([A-Za-z0-9_-]+)", u)
        if m:
            import base64; b = m.group(1)
            try: return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "ignore")
            except Exception: return u
    return u
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None, proxy=({"server": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None),
                          args=["--disable-features=PostQuantumKyber,UseMLKEM,EncryptedClientHello", "--ssl-version-max=tls1.2"])
    ctx = b.new_context(user_agent=UA, locale="en-US"); page = ctx.new_page()
    for q in QUERIES:
        if q in cache and cache[q]: continue
        res = []
        for first in (1, 11, 21):
            try:
                page.goto(f"https://www.bing.com/search?q={quote_plus(q)}&count=10&first={first}&setlang=en&cc=US", wait_until="domcontentloaded", timeout=30000)
                time.sleep(2.0)
                items = page.eval_on_selector_all("li.b_algo h2 a", "els => els.map(e => [e.href, e.textContent])")
                for href, title in items:
                    href = unwrap(href)
                    if href.startswith("http"): res.append({"href": href, "title": (title or "").strip()})
            except Exception as e:
                print(f"[{q}] page {first}: {str(e)[:80]}", flush=True)
            time.sleep(3.0)
        cache[q] = res; json.dump(cache, open(CACHE, "w"))
        print(f"[{q}] {len(res)} results", flush=True)
    b.close()
print("SEARCH_DONE", flush=True)
