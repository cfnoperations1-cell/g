"""Compile med spa / clinic leads that mention peptides, with emails, into a CSV."""
import json, csv, os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import crawl
S=crawl.S if hasattr(crawl,"S") else os.environ.get("VENDOR_CRAWL_DIR",".")
D=S
res={}
for f in sorted(glob.glob(f"{D}/medspa/results*.jsonl")) + sorted(glob.glob(f"{D}/medspa/results*.pw*.jsonl")) + sorted(glob.glob(f"{D}/medspa/results_deep*.jsonl")):
    for l in open(f):
        r=json.loads(l); res[r["domain"]]=r
COLS=["business_name","state","city","website","domain","primary_email","all_emails","email_page","phone","instagram","us_signal_on_site",
      "category","peptide_terms_found","peptide_hits","crawl_status","pages_checked","source_query","notes"]
MEDSPA_TERMS=["med spa","medspa","medical spa","aesthetic","botox","filler","laser","microneedling","iv therapy","wellness","hormone","weight loss"]
out=[]
for dom,r in res.items():
    txtlow=" ".join(r.get("pages_checked") or [])  # not full text; use flags
    emails=crawl.clean_emails(r.get("emails") or [], dom)
    ms=[t for t in MEDSPA_TERMS if False]  # medspa term hits captured at crawl-time via company_type
    notes=[]
    st=r.get("status","")
    if st.startswith(("pw_blocked","blocked")): notes.append("blocks automated access; check manually")
    if st=="robots_disallowed": notes.append("robots.txt disallows")
    ig="@"+r["instagram"] if r.get("instagram") else ""
    src=r.get("source","") or ""
    parts=src.split("|"); qstate=parts[1] if len(parts)>2 else ""; qcity=parts[2] if len(parts)>2 else ""
    state=qstate or (r.get("state") or "")
    out.append({"business_name": r.get("company_name") or dom, "state": state, "city": qcity, "website": f"https://{dom}/", "domain": dom,
        "primary_email": emails[0] if emails else "", "all_emails": "; ".join(emails), "email_page": r.get("email_page",""),
        "phone": "; ".join(r.get("phones") or []), "instagram": ig,
        "us_signal_on_site": "yes" if r.get("us_based") else ("" if not r.get("pages_checked") else "no"),
        "category": r.get("company_type",""), "peptide_terms_found": ", ".join(r.get("peptide_terms") or []), "peptide_hits": len(r.get("peptide_terms") or []),
        "crawl_status": st, "pages_checked": len(r.get("pages_checked") or []),
        "source_query": src.split("|")[0] if src else "", "notes": "; ".join(notes)})
# keep only peptide-mentioning med spa/clinic leads
kept=[r for r in out if r["peptide_hits"]>=1]
kept.sort(key=lambda x:(x["state"] or "ZZ", x["primary_email"]=="", -int(x["peptide_hits"]), x["business_name"].lower()))
os.makedirs(f"{ROOT}/data/out", exist_ok=True)
def w(path, rows):
    with open(path,"w",newline="",encoding="utf-8-sig") as f:
        wr=csv.DictWriter(f, fieldnames=COLS); wr.writeheader(); wr.writerows(rows)
    return len(rows)
stamp=sys.argv[1] if len(sys.argv)>1 else "latest"
na=w(f"{ROOT}/data/out/medspa_peptide_all_{stamp}.csv", kept)
ne=w(f"{ROOT}/data/out/medspa_peptide_with_email_{stamp}.csv", [r for r in kept if r["primary_email"]])
# per-state directory
import collections
os.makedirs(f"{ROOT}/data/out/medspa_by_state_{stamp}", exist_ok=True)
byst=collections.defaultdict(list)
for r in kept: byst[r["state"] or "unknown"].append(r)
for stt,rows in sorted(byst.items()):
    w(f"{ROOT}/data/out/medspa_by_state_{stamp}/medspa_{stt}.csv", rows)
summ=[{"state":k,"leads":len(v),"with_email":sum(1 for x in v if x["primary_email"])} for k,v in sorted(byst.items(), key=lambda kv:-len(kv[1]))]
with open(f"{ROOT}/data/out/medspa_state_summary_{stamp}.csv","w",newline="",encoding="utf-8-sig") as f:
    wr=csv.DictWriter(f, fieldnames=["state","leads","with_email"]); wr.writeheader(); wr.writerows(summ)
print(f"medspa peptide-confirmed={na} with_email={ne} | crawled={len(out)} | states={len(byst)}")
for s2 in summ[:12]: print(f"  {s2['state']:8s} leads={s2['leads']:4d} email={s2['with_email']}")
