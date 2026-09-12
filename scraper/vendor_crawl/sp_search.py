"""Startpage (Google results) via headless Chromium -> cache {query: [{href,title}]}. Slow and polite; backs off on empty pages."""
import os, sys, json, time
from urllib.parse import quote_plus, urlparse
from playwright.sync_api import sync_playwright
S = os.path.dirname(os.path.abspath(__file__))
QF, CACHE = sys.argv[1], sys.argv[2]
QUERIES = [l.strip() for l in open(QF) if l.strip()]
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None, proxy=({"server": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None),
                          args=["--disable-features=PostQuantumKyber,UseMLKEM,EncryptedClientHello", "--ssl-version-max=tls1.2"])
    ctx = b.new_context(user_agent=UA, locale="en-US"); page = ctx.new_page()
    empty_streak = 0
    for i, q in enumerate(QUERIES, 1):
        if q in cache and cache[q]: continue
        res = []
        for attempt in range(3):
            try:
                page.goto("https://www.startpage.com/do/search?q=" + quote_plus(q) + "&cat=web&language=english", wait_until="domcontentloaded", timeout=35000)
                time.sleep(3.5)
                items = page.eval_on_selector_all("a.result-link, a.w-gl__result-title, .result a[href^='http']", "els => els.map(e => [e.href, e.textContent])")
                for href, title in items:
                    d = urlparse(href).netloc.lower()
                    if href.startswith("http") and "startpage.com" not in d: res.append({"href": href, "title": (title or "").strip()[:100]})
                res = list({r["href"]: r for r in res}.values())
            except Exception as e:
                print(f"[{q}] {str(e)[:80]}", flush=True)
            if res: break
            wait = 90 * (attempt + 1); print(f"[{q}] empty (title={page.title()[:40]!r}); backing off {wait}s", flush=True); time.sleep(wait)
            ctx.close(); ctx = b.new_context(user_agent=UA, locale="en-US"); page = ctx.new_page()
        cache[q] = res; json.dump(cache, open(CACHE, "w"))
        empty_streak = 0 if res else empty_streak + 1
        print(f"({i}/{len(QUERIES)}) [{q[:60]}] {len(res)} results", flush=True)
        if empty_streak >= 8: print("giving up: 8 consecutive empty", flush=True); break
        time.sleep(4.0)
    b.close()
print("SEARCH_DONE", flush=True)
