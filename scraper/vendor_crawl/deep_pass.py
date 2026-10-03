"""Deep pass for reachable sites that yielded no email: try every policy/contact/about path
(Shopify + WooCommerce + generic), then a Chromium render of the contact + policy pages."""
import json, os, sys, time, glob
from urllib.parse import urljoin
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crawl
S = crawl.S
res = {}
BASE = [f for f in sorted(glob.glob(f"{S}/results*.jsonl")) if ".pw" not in f and "deep" not in f and "pre_pw" not in f]
for f in BASE + sorted(glob.glob(f"{S}/results*.pw*.jsonl")) + sorted(glob.glob(f"{S}/results_deep*.jsonl")):
    if os.path.exists(f):
        for l in open(f): r = json.loads(l); res[r["domain"]] = r
SHARD, NSHARD = int(sys.argv[1]), int(sys.argv[2]); RUN = sys.argv[3] if len(sys.argv) > 3 else "a"
targets = [r for r in res.values() if r["status"] in ("ok", "ok_chromium") and not r.get("emails") and not r["status"].endswith("+deep")]
targets = [r for i, r in enumerate(sorted(targets, key=lambda r: r["domain"])) if i % NSHARD == SHARD]
print(f"{len(targets)} sites to deep-crawl", flush=True)
PATHS = ["/policies/contact-information", "/policies/privacy-policy", "/policies/terms-of-service", "/policies/refund-policy", "/policies/shipping-policy",
         "/pages/contact", "/pages/contact-us", "/pages/about-us", "/pages/about", "/pages/faq", "/pages/faqs", "/pages/shipping", "/pages/refund-policy",
         "/contact", "/contact-us", "/contactus", "/about", "/about-us", "/privacy-policy", "/privacy", "/terms", "/terms-of-service", "/terms-and-conditions",
         "/refund-policy", "/return-policy", "/shipping-policy", "/faq", "/faqs", "/support", "/help", "/wholesale", "/customer-service", "/legal", "/disclaimer"]
out = open(f"{S}/results_deep_{RUN}{SHARD}.jsonl", "w")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None, proxy=({"server": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None),
                                args=["--disable-features=PostQuantumKyber,UseMLKEM,EncryptedClientHello", "--ssl-version-max=tls1.2"])
    for r in targets:
        dom = r["domain"]; base = r["pages_checked"][0] if r.get("pages_checked") else f"https://{dom}/"
        emails, phones, pages = set(), set(), list(r.get("pages_checked") or [])
        visited = {u.rstrip("/") for u in pages}
        # 1. requests over the full path list
        for path in PATHS:
            u = urljoin(base, path)
            if u.rstrip("/") in visited: continue
            visited.add(u.rstrip("/"))
            if not crawl.robots_ok(dom, u): continue
            time.sleep(1.0)
            h, st = crawl.fetch(u)
            if not h: continue
            _, e, ph, _, _, _ = crawl.extract(h, u)
            pages.append(u); emails |= e; phones |= ph
            if crawl.clean_emails(e, dom):
                r["email_page"] = u; break
        # 2. Chromium render of home + contact-ish pages if still nothing
        if not crawl.clean_emails(emails, dom):
            ctx = browser.new_context(user_agent=crawl.UA, locale="en-US"); page = ctx.new_page()
            for u in [base] + [urljoin(base, x) for x in ("/contact", "/pages/contact", "/contact-us", "/pages/contact-us", "/policies/contact-information", "/privacy-policy", "/policies/privacy-policy")]:
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=25000)
                    try: page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception: pass
                    time.sleep(1.0); h = page.content()
                    # emails revealed by JS (mailto links added after load)
                    hrefs = page.eval_on_selector_all("a[href^='mailto:']", "els => els.map(e => e.getAttribute('href'))")
                    for m in hrefs: emails.add(m[7:].split("?")[0])
                    _, e, ph, _, _, _ = crawl.extract(h, u); emails |= e; phones |= ph
                    if u.rstrip("/") not in visited: pages.append(u); visited.add(u.rstrip("/"))
                    if crawl.clean_emails(e | set(x[7:].split("?")[0] for x in hrefs), dom): r["email_page"] = u; break
                except Exception:
                    continue
                time.sleep(1.0)
            ctx.close()
        r["emails"] = crawl.clean_emails(set(r.get("emails") or []) | emails, dom)
        if phones and not r.get("phones"): r["phones"] = crawl.clean_phones(phones)[:3]
        r["pages_checked"] = pages; r["status"] = r["status"] + "+deep"
        out.write(json.dumps(r) + "\n"); out.flush()
        print(f"{dom:35s} pages={len(pages)} emails={r['emails']}", flush=True)
    browser.close()
out.close(); print("DEEP_DONE", flush=True)
