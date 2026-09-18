"""List queue contacts whose address belongs to a different business than the site.

The crawler records every address it finds on a vendor's pages, and some of those
belong to somebody else entirely: lgipeptides.com publishes its marketing
agency's address, scientificamerican.com publishes its publisher's, and a handful
of sites publish outright placeholders like jane.smith@clinic.com. Writing a
wholesale peptide pitch to any of them reaches the wrong company under Jonathan's
name.

outreach/ingest_resolved.py already refuses these for new leads. This finds the
ones that entered the queue before that gate existed, and writes them to
outreach/third_party_contacts.csv, which serve_send.py skips.

A different TLD or a hyphen is NOT a different business -- baysidepeptides.shop
and baysidepeptides.com are one vendor, as are vanta-peptides.com and
vantapeptides.com -- so the comparison is made on the domain stripped of its TLD
and punctuation.

    python3 outreach/audit_third_party.py           # report only
    python3 outreach/audit_third_party.py --write   # rewrite the skip list
"""
import csv, glob, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "outreach"))
import serve_send as ss

csv.field_size_limit(10_000_000)
OUT = ROOT / "outreach" / "third_party_contacts.csv"


def core(d):
    d = re.sub(r"^www\.", "", (d or "").lower())
    d = re.sub(r"\.[a-z.]{2,12}$", "", d)
    return re.sub(r"[^a-z0-9]", "", d)


def same_business(email_domain, site_domain):
    a, b = core(email_domain), core(site_domain)
    if not a or not b:
        return True
    return a == b or a in b or b in a


def harvested_from():
    """email -> the domain of the site the crawler found it on."""
    src = {}
    for p in sorted(glob.glob(str(ROOT / "scraper/hunts/*.csv"))
                    + glob.glob(str(ROOT / "exports/*.csv"))):
        try:
            rows = list(csv.DictReader(open(p, newline="", encoding="utf-8-sig")))
        except Exception:
            continue
        for r in rows:
            dom = (r.get("domain") or "").strip().lower()
            if not dom:
                continue
            for col in ("email", "primary_email"):
                em = (r.get(col) or "").strip().lower()
                if em and "@" in em:
                    src.setdefault(em, dom)
    return src


def main(write=False):
    src = harvested_from()
    sent = {r["email"].strip().lower(): r["status"] for r in ss.load_sent()}
    rows = []
    for r in ss.load_queue():
        em = r["email"].strip().lower()
        ed = em.split("@", 1)[1]
        sd = src.get(em)
        if not sd or ed in ss.FREEMAIL or same_business(ed, sd):
            continue
        rows.append({"email": em, "site": sd, "status": sent.get(em, "pending")})
    rows.sort(key=lambda r: r["email"])
    pend = sum(1 for r in rows if r["status"] == "pending")
    print(f"{len(rows)} contacts sit on a domain unrelated to the site they came from "
          f"({pend} never emailed)")
    for r in rows:
        print(f"   {r['status']:<9} {r['email']:<44} <- {r['site']}")
    if write:
        with open(OUT, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["email", "site", "status"])
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main("--write" in sys.argv[1:])
