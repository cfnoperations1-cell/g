"""Serve and record the outreach draft queue.

`--build`  : (re)build outreach/draft_queue.csv from the med spa and vendor exports, one row per
             not-yet-contacted verified contact, tagged with audience and its rendered subject/body.
`--next N` : print the next N pending rows (not in sent_log or drafted_log) as JSON for the caller to
             turn into Gmail drafts, interleaving med spa and vendor.
`--record f`: append the addresses in file f (one per line) to outreach/drafted_log.csv.
`--stats`  : counts.
"""
from __future__ import annotations
import argparse, csv, glob, json, os, sys
from pathlib import Path
import config
from emailer.agent import render, load_message
import emailer.medspa_batch as mb
import emailer.vendor_batch as vb

ROOT = Path(config.BASE_DIR)
OUT = ROOT / "outreach"
QUEUE = OUT / "draft_queue.csv"
SENT = OUT / "sent_log.csv"
DRAFTED = OUT / "drafted_log.csv"
COLS = ["audience","email","business_name","to","subject","body"]


def _done() -> set:
    done = set()
    for f in (SENT, DRAFTED):
        if f.exists():
            for r in csv.DictReader(open(f, newline="", encoding="utf-8")):
                done.add(r["email"].lower()); done.add(r.get("domain") or r["email"].split("@")[1])
    if (OUT / "drafted_seed.txt").exists():
        for e in open(OUT/"drafted_seed.txt").read().split():
            if "@" in e: done.add(e.lower()); done.add(e.split("@")[1])
    return done


def build():
    ms_subj, ms_body = load_message(mb.MESSAGE)
    vn_subj, vn_body = load_message(vb.MESSAGE)
    ms_src = sorted(glob.glob(str(ROOT/"exports"/"medspa_peptides_with_email_*.csv")))[-1]
    vn_src = sorted(glob.glob(str(ROOT/"exports"/"us_peptide_vendors_with_email_*.csv")))[-1]
    rows, seen = [], set()
    # med spa: reuse the batch's own selection/scoring for a huge limit
    for score, r, email, dom in mb.pick_leads(10**9, Path(ms_src)):
        if email in seen: continue
        seen.add(email)
        name = mb.clean_name(r["business_name"], dom)
        peps = mb.peptide_phrase(r["peptide_terms_found"])
        rows.append({"audience":"medspa","email":email,"business_name":name,"to":email,
                     "subject": ms_subj.replace("{business_name}",name).replace("{peptides}",peps),
                     "body": render(ms_body.replace("{business_name}",name).replace("{peptides}",peps))})
    for score, r, email, dom in vb.pick_leads(10**9, Path(vn_src)):
        if email in seen: continue
        seen.add(email)
        name = vb.vendor_name(r.get("business_name",""), dom)
        rows.append({"audience":"vendor","email":email,"business_name":name,"to":email,
                     "subject": vn_subj.replace("{business_name}",name),
                     "body": render(vn_body.replace("{business_name}",name))})
    with open(QUEUE,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(rows)
    ms=sum(1 for r in rows if r["audience"]=="medspa"); print(f"queue built: {len(rows)} ({ms} medspa, {len(rows)-ms} vendor)")


def pending_rows():
    done = _done()
    rows = list(csv.DictReader(open(QUEUE, newline="", encoding="utf-8")))
    return [r for r in rows if r["email"].lower() not in done]


def nxt(n):
    pend = pending_rows()
    ms=[r for r in pend if r["audience"]=="medspa"]; vn=[r for r in pend if r["audience"]=="vendor"]
    out=[]; i=j=0
    # interleave ~ proportional, med spa first (higher-value)
    while len(out)<n and (i<len(ms) or j<len(vn)):
        if i<len(ms): out.append(ms[i]); i+=1
        if len(out)<n and j<len(vn): out.append(vn[j]); j+=1
    print(json.dumps([{"audience":r["audience"],"to":r["to"],"subject":r["subject"],"body":r["body"]} for r in out[:n]]))


def record(path):
    emails=[e.strip().lower() for e in open(path).read().split() if "@" in e]
    new = not DRAFTED.exists()
    with open(DRAFTED,"a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        if new: w.writerow(["email","domain","mode"])
        for e in emails: w.writerow([e, e.split("@")[1], "draft"])
    print(f"recorded {len(emails)} drafted")


def stats():
    tot=len(list(csv.DictReader(open(QUEUE)))) if QUEUE.exists() else 0
    print(f"queue={tot} pending={len(pending_rows())} done={len(_done())}")


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--build",action="store_true"); ap.add_argument("--next",type=int); ap.add_argument("--record"); ap.add_argument("--stats",action="store_true")
    a=ap.parse_args()
    if a.build: build()
    elif a.next: nxt(a.next)
    elif a.record: record(a.record)
    else: stats()
