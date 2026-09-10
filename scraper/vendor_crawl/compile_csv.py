"""Build the final CSV around the user's master vendor list: one row per US (or unknown-country) vendor,
enriched with everything the crawl found, plus roster vendors that aren't in the master list."""
import json, csv, os, sys, glob, re
from urllib.parse import urlparse
S = os.environ.get("VENDOR_CRAWL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "vendor_crawl")); os.makedirs(S, exist_ok=True); sys.path.insert(0, S)
import crawl

def norm(n): return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\b(inc|llc|ltd|co|company|the|us|usa)\b\.?", " ", (n or "").lower())).strip()
NON_VENDOR = {"researchgate.net", "atom.com", "facebook.com", "instagram.com", "linkedin.com", "google.com", "youtube.com", "amazon.com"}

def dom_of(u):
    u = (u or "").strip()
    if not u: return ""
    if not u.startswith("http"): u = "https://" + u
    d = urlparse(u).netloc.lower().split(":")[0]
    return d[4:] if d.startswith("www.") else d

# 1. crawl results: base passes, then Chromium shards override by domain
res = {}
BASE = [f for f in sorted(glob.glob(f"{S}/results*.jsonl")) if ".pw" not in f and "deep" not in f and "pre_pw" not in f]
for f in BASE + sorted(glob.glob(f"{S}/results*.pw*.jsonl")) + sorted(glob.glob(f"{S}/results_deep*.jsonl")):
    if not os.path.exists(f): continue
    for l in open(f):
        r = json.loads(l); res[r["domain"]] = r
# 2. name -> domain resolutions (guesser) and roster
name2dom = {}
for f in (f"{S}/guessed.tsv", f"{S}/guessed3.tsv"):
    if os.path.exists(f):
        for l in open(f):
            p = l.rstrip("\n").split("\t")
            if len(p) >= 4 and p[1] and p[2] in ("verified", "blocked_unverified"):
                d = dom_of(p[3]) or p[1]
                if d != "atom.com": name2dom[norm(p[0])] = (d, p[2])
BAD_NAME = re.compile(r"challenge|just a moment|access denied|attention required|404|not found|robot", re.I)
for d, r in res.items():
    for n in (r.get("roster_name"), r.get("company_name")):
        if n and norm(n) and not BAD_NAME.search(n) and d not in NON_VENDOR: name2dom.setdefault(norm(n), (d, "crawled"))
for l in open(os.path.join(ROOT, "scraper", "vendor_domains.tsv")):
    if l.startswith("#") or not l.strip(): continue
    p = l.rstrip("\n").split("\t")
    if len(p) >= 3 and p[2] and p[2].strip() not in NON_VENDOR: name2dom.setdefault(norm(p[0]), (p[2].strip(), "roster"))
known = json.load(open(f"{S}/known_emails.json"))
FR = json.load(open(f"{S}/finnrick/meta.json")) if os.path.exists(f"{S}/finnrick/meta.json") else {}
known_by_dom = {}
for e, d in known.items(): known_by_dom.setdefault(d, []).append(e)

COLS = ["vendor", "country", "website", "domain", "primary_email", "all_emails", "email_source", "email_page", "phone", "whatsapp", "telegram_signal", "social",
        "us_signal_on_site", "state_mentioned", "company_type", "sells_direct", "research_only", "peptide_terms_found", "peptide_hits",
        "peptidebase_tier", "listed_on", "peptidebase_profile", "finnrick_profile", "finnrick_location", "finnrick_products_tested", "finnrick_tests", "finnrick_status", "crawl_status", "pages_checked", "notes"]

def enrich(base, dom):
    r = res.get(dom, {})
    emails = crawl.clean_emails(r.get("emails") or [], dom) if dom else []
    src = "website" if emails else ""
    for e in known_by_dom.get(dom, []) + [x for x in re.split(r"[;,\s]+", base.get("master_email", "").lower()) if "@" in x]:
        if e not in emails: emails.append(e); src = (src + "+" if src else "") + "master_list/drive"
    st = r.get("status", "") if dom else "no_website_found"
    notes = [base.get("master_notes", "")] if base.get("master_notes") else []
    if r.get("content_site"): notes.append("reads like editorial/affiliate site")
    if st.startswith(("pw_blocked", "blocked")): notes.append("site blocks automated access; check contact page manually")
    if st == "robots_disallowed": notes.append("robots.txt disallows crawling; check manually")
    if st.startswith(("error", "http:")): notes.append(f"site unreachable ({st})")
    if base.get("resolution") == "blocked_unverified": notes.append("domain guessed from name, not verified")
    row = dict.fromkeys(COLS, "")
    row.update({
        "vendor": base.get("vendor") or (r.get("company_name") if not re.search(r"challenge|just a moment|access denied|attention required|404|not found", r.get("company_name") or "", re.I) else "") or r.get("roster_name") or dom, "country": base.get("country", ""),
        "website": (f"https://{dom}/" if dom else ""), "domain": dom,
        "primary_email": emails[0] if emails else "", "all_emails": "; ".join(emails), "email_source": src, "email_page": r.get("email_page", ""),
        "phone": "; ".join(r.get("phones") or []) or base.get("master_phone", ""), "whatsapp": base.get("whatsapp", ""),
        "telegram_signal": base.get("telegram", ""), "social": base.get("social", ""),
        "us_signal_on_site": "yes" if r.get("us_based") else ("" if not r.get("pages_checked") else "no"),
        "state_mentioned": r.get("state") or "", "company_type": r.get("company_type", ""),
        "sells_direct": "yes" if r.get("sells_direct") else "", "research_only": "yes" if r.get("research_only_evidence") else "",
        "peptide_terms_found": ", ".join(r.get("peptide_terms") or []), "peptide_hits": len(r.get("peptide_terms") or []),
        "peptidebase_tier": base.get("tier", ""), "listed_on": base.get("listed_on", ""), "peptidebase_profile": base.get("pb", ""), "finnrick_profile": base.get("fr", ""),
        "crawl_status": st, "pages_checked": len(r.get("pages_checked") or []), "notes": "; ".join(n for n in notes if n),
    })
    fm = FR.get(dom)
    if fm:
        row["finnrick_profile"] = row["finnrick_profile"] or f"https://www.finnrick.com/vendors/{fm['slug']}"
        row["finnrick_location"], row["finnrick_products_tested"], row["finnrick_tests"], row["finnrick_status"] = fm["location"], fm["product_count"], fm["test_count"], fm["status"]
        if not row["listed_on"] or "Finnrick" not in row["listed_on"]: row["listed_on"] = (row["listed_on"] + ", " if row["listed_on"] else "") + "Finnrick"
        if row["country"].startswith("unknown") or not row["country"] or "(roster)" in row["country"]:
            if re.search(r"\bus\b", fm["location"], re.I): row["country"] = "United States (Finnrick)"
            elif fm["location"]: row["country"] = f"{fm['location']} (Finnrick)"
    return row

out, seen = [], set()
master = list(csv.DictReader(open(os.path.join(ROOT, "scraper", "peptide_vendors_master.csv"), encoding="utf-8-sig")))
for m in master:
    c = m["Country"].strip()
    if c and not c.startswith("United States"): continue
    dom = dom_of(m["Website"]); resolution = "master_website"
    if not dom:
        dom, resolution = name2dom.get(norm(m["Vendor"]), ("", ""))
    if dom in NON_VENDOR: dom, resolution = "", ""
    base = {"vendor": m["Vendor"], "country": c or "unknown (not stated on list)", "master_email": m["Email"], "master_phone": m["Phone"], "whatsapp": m["WhatsApp"],
            "telegram": m["Telegram/Signal"], "social": m["Social"], "tier": m["PeptideBase Tier"], "listed_on": m["Listed On"], "pb": m["PeptideBase Profile"],
            "fr": m["Finnrick Profile"], "master_notes": m["Notes"], "resolution": resolution}
    out.append(enrich(base, dom)); seen.add(dom)
# roster / seed vendors not in the master list
for dom, r in res.items():
    if dom in seen or not dom or dom in NON_VENDOR: continue
    cname = r.get("company_name") or ""
    if BAD_NAME.search(cname): cname = ""
    fm = FR.get(dom, {})
    base = {"vendor": fm.get("name") or r.get("roster_name") or cname or dom,
            "country": "United States (roster)" if r.get("source") in ("seed_urls", "vendor_domains") else ("unknown (not stated on list)" if fm else ""),
            "listed_on": "Finnrick" if fm else f"repo roster ({r.get('source','')})"}
    out.append(enrich(base, dom)); seen.add(dom)

def us_ok(r): return r["country"].startswith("United States") or r["us_signal_on_site"] == "yes"
out = [r for r in out if us_ok(r) or r["country"].startswith("unknown")]
out.sort(key=lambda x: (x["primary_email"] == "", 0 if x["country"].startswith("United States") else 1, -int(x["finnrick_products_tested"] or 0), -int(x["peptide_hits"]), x["vendor"].lower()))
os.makedirs(f"{ROOT}/data/out", exist_ok=True)
def write(path, rows_):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(rows_)
    return len(rows_)
stamp = sys.argv[1] if len(sys.argv) > 1 else "latest"
n_all = write(f"{ROOT}/data/out/us_peptide_vendors_all_{stamp}.csv", out)
n_em = write(f"{ROOT}/data/out/us_peptide_vendors_with_email_{stamp}.csv", [r for r in out if r["primary_email"] and (r["country"].startswith("United States") or r["us_signal_on_site"] == "yes")])
us_rows = [r for r in out if r["country"].startswith("United States")]
print(f"rows={n_all} with_email={n_em} | master-US={len(us_rows)} master-US-with-email={sum(1 for r in us_rows if r['primary_email'])} | no_website={sum(1 for r in out if not r['domain'])}")
from collections import Counter; print(Counter(r["crawl_status"].split(":")[0] for r in out))
