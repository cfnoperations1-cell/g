#!/usr/bin/env python3
"""Build the Marinexis CRM page (exports/crm_artifact.html).

Jonathan, Sep 28: "integrate a new CRM into the back end to see everything
about who we contacted and more and who's ordered."

The page has two halves:

  * Contact history, from the repo's own state files: sent_log.csv (who was
    emailed, when, follow-ups, bounced/replied), reply_notes.csv (what they
    said), catalog_sent.csv (who got the catalog) and the business name from
    draft_queue.csv. It is embedded in the page at build time, so it is exactly
    as current as the last wave. Rebuild after every wave, like dash_build.py.
  * Jonathan's own entries -- stage changes, next steps, notes, orders -- are
    saved by the page into its artifact database (collection "accounts", one
    document per email). They survive every rebuild and republish. Read them
    back with ArtifactData list on "accounts".

The starting stage is derived from the record and never overrides a stage
Jonathan set by hand.

    python3 outreach/crm_build.py
"""
import csv, json, re
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(10_000_000)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outreach"
TEMPLATE = ROOT / "exports" / "crm_template.html"
TARGET = ROOT / "exports" / "crm_artifact.html"


def rows(path):
    p = OUT / path
    return list(csv.DictReader(open(p, encoding="utf-8-sig"))) if p.exists() else []


def stage_of(status, note, catalog):
    """Starting stage from the record. reply_notes.csv is free text written wave
    by wave, so this reads the leading label each note was written with."""
    n = (note or "").strip().upper()
    if status == "bounced":
        return "bounced"
    if re.match(r"(DECLINED|NOT INTERESTED|UNSUBSCRIBE|PRICE REJECTED)", n):
        return "declined"
    if n.startswith("READY TO BUY"):
        return "negotiating"
    if catalog or "CATALOG SENT" in n:
        return "catalog"
    if re.match(r"(INTERESTED|QUESTION|NEEDS JONATHAN|FOR JONATHAN|JONATHAN IS HANDLING|(VENDOR - )?NEEDS ANSWER)", n):
        return "interested"
    if n.startswith("AUTO"):
        return "contacted"          # an auto-responder is not a reply
    if status == "replied":
        return "replied"
    return "contacted"


# Queued leads shown in the CRM (Jonathan, Sep 30: "break it down into the CRM").
# Only the audiences still in play: the RUO vendors the sender will reach, the
# practice list he supplied (held behind them), and the telehealth leads held
# on the 503A question. The paused med spa backlog stays out of the CRM.
HOLD_NOTE = {
    "vendor": "Queued: goes out in the daily RUO waves.",
    "practice": "Queued behind the RUO vendors; starts when they run out, best-ranked first.",
    "telehealth": "Held until Jonathan decides how to handle 503A for telehealth.",
}


def queued_leads(already):
    import glob, sys
    sys.path.insert(0, str(OUT))
    import serve_send as ss
    info = {}
    for f in sorted(glob.glob(str(ROOT / "scraper" / "hunts" / "practice_list_*.csv"))):
        if f.endswith(("_rejected.csv", "_catchall.csv")):
            continue
        for r in csv.DictReader(open(f, encoding="utf-8")):
            addr = (r.get("address") or "").split(",")
            city = ", ".join(x.strip() for x in addr[-3:-1]) if len(addr) >= 3 else ""
            info[r["email"].strip().lower()] = {
                "person": " ".join(x for x in (r.get("first_name", ""), r.get("last_name", "")) if x).strip(),
                "role": r.get("position", ""), "ptype": r.get("practice", "").capitalize() if r.get("practice") else "",
                "phone": r.get("phone", ""), "city": re.sub(r"\s+\d{5}(-\d{4})?$", "", city)}
    for f in sorted(glob.glob(str(OUT / "personal_leads_*.csv"))):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            info.setdefault(r["email"].strip().lower(), {"person": r.get("person", ""), "role": r.get("role", "")})
    out, seen = [], set(already)
    saved = ss.AUDIENCE_ONLY
    try:
        for aud in ("vendor", "practice", "telehealth"):
            ss.AUDIENCE_ONLY = aud
            for r in ss.initial_candidates(ss.load_sent()):
                e = r["email"].strip().lower()
                if e in seen:
                    continue
                seen.add(e)
                row = {"email": e, "domain": e.split("@", 1)[1], "name": r.get("business_name") or e,
                       "audience": aud, "sent": "", "last": "", "fu": 0, "status": "queued",
                       "reply": "", "catalog": None, "stage": "queued", "hold": HOLD_NOTE[aud]}
                row.update({k: v for k, v in info.get(e, {}).items() if v})
                out.append(row)
    finally:
        ss.AUDIENCE_ONLY = saved
    return out


def main():
    names = {}
    for r in rows("draft_queue.csv"):
        e = r["email"].strip().lower()
        if e and r.get("business_name"):
            names.setdefault(e, r["business_name"].strip())
    notes = {}
    for r in rows("reply_notes.csv"):
        e = (r.get("email") or "").strip().lower()
        if e and r.get("note"):
            notes[e] = (notes[e] + "\n" + r["note"]) if e in notes else r["note"]
    catalogs = {}
    for r in rows("catalog_sent.csv"):
        e = (r.get("email") or "").strip().lower()
        if e:
            catalogs[e] = {"date": r.get("sent", ""), "pkg": r.get("package", ""), "note": r.get("note", ""),
                           "company": r.get("company", "")}

    contacts = []
    for r in rows("sent_log.csv"):
        e = r["email"].strip().lower()
        if not e:
            continue
        cat = catalogs.get(e)
        note = notes.get(e, "")
        name = names.get(e) or (cat or {}).get("company") or r.get("domain") or e
        contacts.append({
            "email": e, "domain": r.get("domain", ""), "name": name,
            "audience": r.get("audience", ""), "sent": r.get("sent_at", ""), "last": r.get("last_touch_at", ""),
            "fu": int(r.get("stage") or 0), "status": r.get("status", ""),
            "reply": note, "catalog": ({k: v for k, v in cat.items() if k != "company"} if cat else None),
            "stage": stage_of(r.get("status", ""), note, cat),
        })

    contacts += queued_leads({c["email"] for c in contacts})

    data = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "contacts": contacts}
    blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob, 1)
    TARGET.write_text(html, encoding="utf-8")
    from collections import Counter
    c = Counter(x["stage"] for x in contacts)
    print(f"crm built: {len(contacts)} contacts; " + ", ".join(f"{k}={v}" for k, v in c.most_common()))


if __name__ == "__main__":
    main()
