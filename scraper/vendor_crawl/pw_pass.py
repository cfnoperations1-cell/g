"""Second pass with headless Chromium for domains the plain fetch could not read (Cloudflare challenge,
JS-only pages, odd status codes). Serial, one browser, polite delay. Rewrites those rows in results.jsonl."""
import json, os, sys, time, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl
ROOT = crawl.ROOT
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
S = os.environ.get("VENDOR_CRAWL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "vendor_crawl")); os.makedirs(S, exist_ok=True)
SRC = sys.argv[1]; SHARD, NSHARD = int(sys.argv[2]), int(sys.argv[3]); OUT = f"{SRC}.pw{SHARD}.jsonl"
rows = [json.loads(l) for l in open(SRC)]
done = set()
if os.path.exists(OUT):
    done = {json.loads(l)["domain"] for l in open(OUT)}
retry = [r for r in rows if (r["status"].startswith(("blocked", "http:", "error", "nonhtml")) or (r["status"] == "ok" and r.get("text_len", 0) < 400))]
retry = [r for i, r in enumerate(retry) if i % NSHARD == SHARD and r["domain"] not in done]
outf = open(OUT, "a")
print(f"{len(retry)} domains to retry with Chromium", flush=True)

def get(page, url):
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try: page.wait_for_load_state("networkidle", timeout=4000)
        except Exception: pass
        time.sleep(1.0)
        h = page.content()
        if "<title>Just a moment" in h or "challenge-platform" in h:
            time.sleep(5); h = page.content()
        return h, (resp.status if resp else 0)
    except Exception as e:
        return "", f"error:{type(e).__name__}"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None, proxy=({"server": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None), args=["--disable-features=PostQuantumKyber,UseMLKEM,EncryptedClientHello", "--ssl-version-max=tls1.2"])
    for r in retry:
        dom = r["domain"]
        ctx = browser.new_context(user_agent=crawl.UA, locale="en-US"); page = ctx.new_page()
        base = f"https://{dom}/"
        html, st = get(page, base)
        if not html or "<title>Just a moment" in html:
            r["status"] = f"pw_blocked:{st}"; ctx.close(); outf.write(json.dumps(r) + "\n"); outf.flush(); print(f"{dom:35s} {r['status']}", flush=True); continue
        texts, emails, phones, pages = [], set(), set(), [base]
        t, e, ph, links, title, site_name = crawl.extract(html, base)
        texts.append(t); emails |= e; phones |= ph
        r["company_name"] = r.get("company_name") or (site_name or title.split(" | ")[0].split(" - ")[0]).strip()[:80]
        if crawl.clean_emails(e, dom): r["email_page"] = base
        visited = {base.rstrip("/")}
        def rank(u):
            ul = u.lower(); return min((i for i, k in enumerate(crawl.PRIORITY) if k in ul), default=99)
        linked = [u for u in sorted(set(links), key=rank) if rank(u) < 99][:4]
        fixed = [urljoin(base, f) for f in crawl.FIXED[:8]]
        n_fixed = 0
        for u in linked + fixed:
            if u.rstrip("/") in visited: continue
            if u not in linked:
                if n_fixed >= 3 or crawl.clean_emails(emails, dom): break
                n_fixed += 1
            visited.add(u.rstrip("/"))
            if not crawl.robots_ok(dom, u): continue
            time.sleep(1.2)
            h, _ = get(page, u)
            if not h: continue
            t, e, ph, _, _, _ = crawl.extract(h, u)
            texts.append(t); emails |= e; phones |= ph; pages.append(u)
            if crawl.clean_emails(e, dom) and not r.get("email_page"): r["email_page"] = u
        ctx.close()
        full = " ".join(texts); low = full.lower()
        r.update({"status": "ok_chromium", "pages_checked": pages, "emails": crawl.clean_emails(emails, dom), "phones": crawl.clean_phones(phones)[:3],
                  "peptide_terms": sorted({k for k in crawl.PEPTIDE_TERMS if k in low}), "text_len": len(full)})
        us, state = crawl.guess_us_presence(full); r["us_based"], r["state"] = us, state
        ro = next((x for x in crawl.RESEARCH_ONLY if x in low), None); r["research_only_evidence"] = ro
        r["sells_direct"] = crawl.sells_direct(full); r["manufactures"] = crawl.manufactures(full); r["content_site"] = crawl.is_content_site(full)
        r["company_type"] = crawl.classify_company_type(full, ro) if r["peptide_terms"] else "not_peptide_related"
        outf.write(json.dumps(r) + "\n"); outf.flush()
        print(f"{dom:35s} ok_chromium pages={len(pages)} emails={r['emails']}", flush=True)
    browser.close()
outf.close()
print("PW_DONE", flush=True)
