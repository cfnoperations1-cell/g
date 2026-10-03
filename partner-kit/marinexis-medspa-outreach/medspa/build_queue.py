"""Promote enriched med spas into the send queue.

Only rows that clear every gate below get added to outreach/medspa_queue.csv:

  - a usable contact email was found
  - the site actually mentions peptides we supply (MIN_HITS terms)
  - there is a US signal on the page (state, ZIP or US phone)
  - the domain is not foreign-looking
  - nobody on the team has emailed that address or domain before
    (outreach/sent_log.csv + outreach/do_not_contact.csv)

    python3 medspa/build_queue.py            # show what would be added
    python3 medspa/build_queue.py --apply    # actually append to the queue
"""
import csv, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "outreach"))
import serve_send as ss                                    # reuse the same filters the sender uses

ENR = ROOT / "medspa" / "enriched.csv"
QUEUE = ROOT / "outreach" / "medspa_queue.csv"
QCOLS = ["email", "domain", "business_name", "peptides", "city", "state", "phone", "website", "source"]
MIN_HITS = 1


def load_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main(apply=False):
    enriched = load_csv(ENR)
    if not enriched:
        sys.exit("no medspa/enriched.csv -- run medspa/enrich.py first")

    queue = load_csv(QUEUE)
    blocked = ss.load_dnc()
    for r in ss.load_sent():
        blocked.add(r["email"])
        blocked.add(r["domain"])
    for r in queue:
        blocked.add(r["email"].strip().lower())
        blocked.add(r["domain"].strip().lower())

    add, reasons = [], {"no_email": 0, "no_peptides": 0, "not_us": 0, "foreign": 0, "duplicate": 0}
    for r in enriched:
        email = (r.get("email") or "").strip().lower()
        domain = (r.get("domain") or "").strip().lower()
        name = (r.get("business_name") or "").strip()
        if not email or "@" not in email:
            reasons["no_email"] += 1
            continue
        if int(r.get("peptide_hits") or 0) < MIN_HITS:
            reasons["no_peptides"] += 1
            continue
        if not (r.get("us_signal") or "").strip():
            reasons["not_us"] += 1
            continue
        if ss.is_foreign(domain, name) or email.split("@", 1)[0] in ss.FOREIGN_LOCAL:
            reasons["foreign"] += 1
            continue
        if email in blocked or domain in blocked:
            reasons["duplicate"] += 1
            continue
        blocked.add(email)
        blocked.add(domain)
        add.append({
            "email": email, "domain": domain,
            "business_name": ss.clean_name(name, domain),
            "peptides": (r.get("peptides") or "peptides").strip() or "peptides",
            "city": (r.get("city") or "").strip(), "state": (r.get("state") or "").strip(),
            "phone": (r.get("phone") or "").strip(),
            "website": (r.get("url") or f"https://{domain}/").strip(),
            "source": "discovered",
        })

    print(f"enriched={len(enriched)} queue_now={len(queue)} eligible_to_add={len(add)}")
    print("skipped:", reasons)
    for r in add[:15]:
        where = f"{r['city']}, {r['state']}".strip(", ") or "-"
        print(f"  + {r['email']:<38} {r['business_name'][:34]:<34} {where}")
    if len(add) > 15:
        print(f"  ... and {len(add) - 15} more")

    if not apply:
        print("\n(dry run -- re-run with --apply to append these to outreach/medspa_queue.csv)")
        return
    if not add:
        print("nothing to add")
        return
    with open(QUEUE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QCOLS)
        if not queue:
            w.writeheader()
        for r in add:
            w.writerow(r)
    print(f"\nappended {len(add)} -> {QUEUE} (queue now {len(queue) + len(add)})")


if __name__ == "__main__":
    main("--apply" in sys.argv[1:])
