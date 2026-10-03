#!/usr/bin/env python3
"""Import a purchased practice contact list (Jonathan, Sep 29: "Add these leads in").

The list (scraper/lists/Medspa_US_2.csv) has one named contact per row -- owner,
founder, manager -- with an email the list vendor checked against the mail
server: Email_Code "ok" / "Accepted", or "mb" / "Catch-All" when the server
accepts anything and the address could not be confirmed. The address is the one
the list prints; nothing is constructed here.

Jonathan, Sep 29, when the first version started crawling each site: "Don't read
the sites do it based on the type of doctor." So a row qualifies on the list's
own Category, not on its website. That is his decision for this list and it
replaces the usual "site must name a compound we supply" gate here. It also
means the med spa template's "I saw you offer X" cannot be used -- nothing
confirms what X is -- so these rows use emailer/message_practice.txt, which
speaks to the type of practice instead and greets the named contact.

Gates that still apply:
  * US only (the list's own address field).
  * Category on KEEP below.
  * Not already emailed, queued, or declined (serve_send's own checks), and one
    contact per business (an owner/founder is preferred over a manager).
  * Catch-all addresses are held in a separate file, not queued: the domain has
    been throttled for bounces once already.

Rows go into outreach/draft_queue.csv as audience "practice". Nothing sends them
until AUDIENCE_ONLY=practice is chosen.

    python3 scraper/import_medspa_list.py [--apply]
"""
import csv, re, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outreach"))
import serve_send as ss

SRC = ROOT / "scraper" / "lists" / "Medspa_US_2.csv"
HUNTS = ROOT / "scraper" / "hunts"
OUT = HUNTS / "practice_list_2026-09-29.csv"
REJ = HUNTS / "practice_list_2026-09-29_rejected.csv"
CATCH = HUNTS / "practice_list_2026-09-29_catchall.csv"
QUEUE = ROOT / "outreach" / "draft_queue.csv"
TPL = ROOT / "emailer" / "message_practice.txt"
QCOLS = ["audience", "email", "business_name", "to", "subject", "body"]
HCOLS = ["vendor", "domain", "email", "first_name", "last_name", "position", "category", "practice",
         "phone", "address", "status", "source"]

# Category -> how the email names the practice ("... to {practice} practices").
KEEP = {
    "medical spa": "med spa", "health spa": "med spa", "spa and health club": "med spa",
    "wellness center": "wellness", "wellness program": "wellness",
    "weight loss service": "weight-loss",
    "medical clinic": "medical", "walk-in clinic": "medical", "specialized clinic": "medical",
    "skin care clinic": "aesthetics", "dermatologist": "dermatology",
    "plastic surgeon": "plastic surgery", "plastic surgery clinic": "plastic surgery",
    "cosmetic surgeon": "cosmetic surgery",
    "naturopathic practitioner": "integrative and naturopathic",
    "women's health clinic": "women's health", "reproductive health clinic": "women's health",
    "urologist": "urology and men's health", "urology clinic": "urology and men's health",
    "sports medicine clinic": "sports medicine", "sports medicine physician": "sports medicine",
    "orthopedic clinic": "orthopedic and sports medicine",
    "hair transplantation clinic": "hair restoration",
}
PREFER = re.compile(r"owner|founder|ceo|chief|president|principal|medical director|director", re.I)
NAME_OK = re.compile(r"^[A-Za-z][A-Za-z'\-]{1,20}$")


def dom_of(site):
    d = re.sub(r"^https?://", "", (site or "").strip().lower()).split("/")[0]
    return d[4:] if d.startswith("www.") else d


def render(name, first, practice):
    raw = TPL.read_text(encoding="utf-8")
    first_line, rest = raw.split("\n", 1)
    subject = first_line.split(":", 1)[1].strip()
    body = rest.lstrip("\n").rstrip("\n")
    sd = ss.DEFAULT_SENDER
    greet = f"Hi {first}," if first else "Hi there,"
    sub = {"{greeting}": greet, "{business_name}": name, "{practice}": practice,
           "{sender_email}": sd["SENDER_EMAIL"], "{sender_company}": sd["SENDER_COMPANY"],
           "{sender_postal_address}": sd["SENDER_POSTAL_ADDRESS"], "{unsubscribe_line}": ss.UNSUB_LINE}
    for k, v in sub.items():
        subject, body = subject.replace(k, v), body.replace(k, v)
    return subject, body


def main(apply_it):
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig", errors="replace")))
    sent = ss.load_sent()
    sent_emails = {r["email"].strip().lower() for r in sent}
    brands = {ss.brand_key(r["domain"]) for r in sent} - {""}
    queue = ss.load_queue()
    queued = {r["email"].strip().lower() for r in queue}
    brands |= {ss.brand_key(r["email"].split("@", 1)[-1]) for r in queue} - {""}

    rej, catch, by_dom = [], [], {}
    for r in rows:
        em = (r.get("Emails") or "").strip().lower()
        d = dom_of(r.get("Website"))
        cat = (r.get("Category") or "").strip()
        why = None
        if not em or "@" not in em:
            why = "no email"
        elif not r.get("Address", "").strip().endswith("United States"):
            why = "not US (list address)"
        elif cat.lower() not in KEEP:
            why = f"practice type not targeted: {cat or '(blank)'}"
        elif em in sent_emails or em in queued:
            why = "already emailed or queued"
        elif ss.brand_key(d) and ss.brand_key(d) in brands:
            why = "business already emailed or queued"
        elif ss.skip_contact(em, "practice", em.split("@", 1)[-1], r.get("Name", "")):
            why = "excluded by serve_send"
        if why:
            rej.append({"domain": d, "email": em, "reason": why}); continue
        if (r.get("Email_Code") or "").strip().lower() != "ok":
            catch.append(r); continue
        by_dom.setdefault(d, []).append(r)

    keep, qrows = [], []
    for d, people in by_dom.items():
        people.sort(key=lambda p: 0 if PREFER.search(p.get("Position", "")) else 1)
        p = people[0]
        for x in people[1:]:
            rej.append({"domain": d, "email": x["Emails"].strip().lower(), "reason": "second contact at a business"})
        first = (p.get("First Name") or "").strip()
        first = first.capitalize() if NAME_OK.match(first) else ""
        practice = KEEP[p["Category"].strip().lower()]
        # The list's Name is the business's own listing name (Google Maps style),
        # not a scraped page title, so it is used as given. clean_vendor's
        # name-must-resemble-domain check would replace "H&E Medical Aesthetics"
        # with "hemedicalaesthetics.com".
        name = re.sub(r"\s+", " ", p["Name"]).strip()
        # "Holistic Elegance - Medical Aesthetics & Wellness": keep the name, drop
        # the tagline listings append after a dash, bar or colon.
        head = re.split(r"\s+[-|–—]\s+|:\s+", name, maxsplit=1)[0].strip()
        if len(head) >= 4 and ss.usable_name(head):
            name = head
        if not ss.usable_name(name) or len(name) > 60:
            name = d
        subject, body = render(name, first, practice)
        em = p["Emails"].strip().lower()
        keep.append({"vendor": name, "domain": d, "email": em, "first_name": first,
                     "last_name": p.get("Last Name", ""), "position": p.get("Position", ""),
                     "category": p["Category"], "practice": practice, "phone": p.get("Phone", ""),
                     "address": p.get("Address", ""), "status": "ok",
                     "source": "Medspa_US_2.csv (Jonathan, Sep 29; server-verified address)"})
        qrows.append({"audience": "practice", "email": em, "business_name": name, "to": em,
                      "subject": subject, "body": body})

    print(f"{len(rows)} rows -> {len(keep)} practices to queue, {len(catch)} catch-all held, {len(rej)} rejected")
    print("by practice type:", Counter(k["practice"] for k in keep).most_common())
    print("named greeting:", sum(1 for k in keep if k["first_name"]), "of", len(keep))
    print("rejections:", Counter(re.sub(r":.*", "", x["reason"]) for x in rej).most_common())
    if not apply_it:
        print("\ndry run -- pass --apply to write the hunt files and append to the queue")
        if qrows:
            print("\nSAMPLE:\n" + qrows[0]["subject"] + "\n\n" + qrows[0]["body"])
        return
    for path, cols, data in ((OUT, HCOLS, keep), (REJ, ["domain", "email", "reason"], rej)):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(data)
    with open(CATCH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(catch)
    with open(QUEUE, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=QCOLS).writerows(qrows)
    print(f"appended {len(qrows)} rows to {QUEUE}")


if __name__ == "__main__":
    main("--apply" in sys.argv)
