#!/usr/bin/env python3
"""Sort outreach/draft_queue.csv into send order: best leads first.

Jonathan, Sep 22: "Choose the best leads top rated locations and verified
emails to the top lowest ranked vendors to bottom."

serve_send.py already walks the queue in file order, so ordering the FILE is
the whole mechanism -- no scoring code runs at send time and nothing about
how a message is built changes. Re-runnable: it reads the queue, scores, and
rewrites it in place.

What "top rated" can honestly mean here. There are no star ratings anywhere in
this data, so none are invented. What we do hold is evidence of prominence and
of substance, and the score is built only from that:

  prominence   Instagram follower count, from the two ranked lists Jonathan
               supplied. The med spa list is itself ranked by followers, so
               this is his own ordering carried through. Log-scaled: the gap
               between 600k and 300k followers should not swamp everything
               else in the score.
  directory    A peptidebase tier or a Finnrick listing means a third party
               catalogued the business. Weak evidence, but real.
  substance    peptide_hits from the crawl -- how many compounds and category
               terms the site actually showed. A clinic whose own pages name
               six of our compounds is a better lead than one that matched two.
  location     A published US address or phone. "Top rated locations" is read
               as a real, locatable practice rather than a rating we lack.
  email        A role or named address on the business's own domain outranks a
               freemail one. This is the only sense in which an address here is
               "verified" -- that it belongs to the business's own domain, on a
               page that published it. Deliverability is NOT verified; nothing
               short of sending establishes that, and this campaign does not
               probe inboxes to find out.

Vendors sort below every med spa regardless of score, which is the "lowest
ranked vendors to bottom" half of the instruction and also matches this week's
med-spa-only focus.
"""
import csv, glob, math, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "outreach" / "draft_queue.csv"
COLS = ["audience", "email", "business_name", "to", "subject", "body"]

FREEMAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
            "aol.com", "proton.me", "protonmail.com", "me.com", "live.com", "msn.com"}
# Addresses a business publishes for being contacted, as opposed to one person's.
ROLE = re.compile(r"^(info|contact|hello|support|sales|admin|office|team|orders?|"
                  r"inquiries|inquiry|clientcare|customerservice|frontdesk|booking|care)\b")


def load(pattern, key):
    """Index every row of every matching CSV by a lowercased key column."""
    idx = {}
    for f in glob.glob(str(ROOT / pattern)):
        try:
            rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
        except Exception:
            continue
        for r in rows:
            k = (r.get(key) or "").strip().lower()
            if k and k not in idx:
                idx[k] = r
    return idx


def followers_by_email():
    """Follower counts out of Jonathan's two Instagram markdown tables."""
    out = {}
    for f in glob.glob(str(ROOT / "scraper/instagram/*.md")):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            em = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", line)
            if not em:
                continue
            n = re.search(r"\|\s*([\d,]{4,})\s*\|", line)          # the follower cell
            if n:
                out[em.group(0).lower()] = int(n.group(1).replace(",", ""))
    return out


def score(row, hunts, exports, followers):
    em = row["email"].strip().lower()
    dom = em.split("@", 1)[1] if "@" in em else ""
    h = hunts.get(em) or {}
    x = exports.get(em) or {}
    s, why = 0.0, []

    f = followers.get(em, 0)
    if f:
        s += min(30.0, 6.0 * math.log10(max(f, 10)))               # 10k -> 24, 600k -> 30
        why.append(f"{f:,} followers")

    tier = (x.get("peptidebase_tier") or "").strip()
    if tier.isdigit():
        s += min(10.0, int(tier) * 2.0); why.append(f"tier {tier}")
    if (x.get("finnrick_profile") or x.get("finnrick_status") or "").strip():
        s += 4.0; why.append("Finnrick listed")
    if (x.get("listed_on") or "").strip():
        s += 2.0

    hits = (h.get("peptide_hits") or x.get("peptide_hits") or "").strip()
    if hits.isdigit():
        s += min(12.0, int(hits) * 2.0); why.append(f"{hits} peptide hits")

    if (h.get("us_signal") or x.get("us_signal_on_site") or "").strip().lower() in ("yes", "y", "true"):
        s += 6.0; why.append("published US location")
    if (x.get("state_mentioned") or "").strip():
        s += 2.0

    if dom and dom not in FREEMAIL:
        s += 8.0; why.append("address on own domain")
        if ROLE.match(em.split("@", 1)[0]):
            s += 2.0
    else:
        why.append("freemail address")

    # A named compound we actually supply beats a bare category phrase.
    peps = (h.get("peptides") or "").strip()
    if peps and "GLP-1 weight loss peptides" not in peps:
        s += 5.0; why.append("names compounds we supply")
    elif peps:
        s += 2.0
    return s, "; ".join(why)


def main():
    apply = "--apply" in sys.argv
    rows = list(csv.DictReader(open(QUEUE, newline="", encoding="utf-8")))
    hunts = load("scraper/hunts/*.csv", "email")
    exports = load("exports/us_peptide_vendors_*.csv", "primary_email")
    followers = followers_by_email()
    print(f"{len(rows)} queue rows; evidence: {len(hunts)} hunt, {len(exports)} export, "
          f"{len(followers)} follower counts")

    scored = []
    for i, r in enumerate(rows):
        sc, why = score(r, hunts, exports, followers)
        # med spas first as a block, then vendors; score orders within each.
        scored.append(((0 if r["audience"] == "medspa" else 1), -sc, i, r, sc, why))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))

    print("\nTOP 12:")
    for _, _, _, r, sc, why in scored[:12]:
        print(f"  {sc:5.1f}  {r['audience']:6s} {r['business_name'][:30]:32s} {r['email'][:36]:38s} {why}")
    print("\nBOTTOM 6:")
    for _, _, _, r, sc, why in scored[-6:]:
        print(f"  {sc:5.1f}  {r['audience']:6s} {r['business_name'][:30]:32s} {r['email'][:36]:38s} {why}")

    ms = [t for t in scored if t[0] == 0]
    print(f"\nmed spas {len(ms)} (best {-ms[0][1]:.1f}, worst {-ms[-1][1]:.1f}); "
          f"vendors {len(scored) - len(ms)} below them")
    if not apply:
        print("\ndry run -- pass --apply to rewrite the queue in this order")
        return
    with open(QUEUE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for t in scored:
            w.writerow({k: t[3][k] for k in COLS})
    print(f"\nrewrote {QUEUE} in send order")


if __name__ == "__main__":
    main()
