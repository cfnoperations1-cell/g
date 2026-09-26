#!/usr/bin/env python3
"""Sort outreach/draft_queue.csv into send order: best leads first.

Jonathan, Sep 22: "Choose the best leads top rated locations and verified
emails to the top lowest ranked vendors to bottom."

serve_send.py already walks the queue in file order, so ordering the FILE is
the whole mechanism -- no scoring code runs at send time and nothing about
how a message is built changes. Re-runnable: it reads the queue, scores, and
rewrites it in place.

What "top rated" means here. Star ratings exist only where a directory
publishes one: dir_mine.py records the listing's schema.org aggregateRating (or
visible "4.9/5") into scraper/ratings.csv as it reads each page. Rows mined
before Sep 23 have no rating on file, and nothing is invented for them. The
score is built only from evidence actually held:

  rating       Review-weighted, not raw. A plain average ranks a 5.0 from three
               reviews above a 4.8 from four hundred, which is backwards, so the
               rating is shrunk toward the directory mean in proportion to how
               few reviews stand behind it (a Bayesian average, prior weight
               RATING_PRIOR_N reviews at RATING_PRIOR_MEAN). Review volume also
               earns a little on its own: a practice with a thousand reviews is
               an established business.

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


RATINGS_FILE = ROOT / "scraper" / "ratings.csv"
RATING_PRIOR_N = 25       # a rating needs ~25 reviews before it is mostly its own
RATING_PRIOR_MEAN = 4.6   # where med spa ratings sit before evidence says otherwise


def load_ratings():
    out = {}
    if RATINGS_FILE.exists():
        for r in csv.DictReader(open(RATINGS_FILE, encoding="utf-8")):
            try:
                v = float(r["rating"])
            except (TypeError, ValueError):
                continue
            n = int(r["reviews"]) if (r.get("reviews") or "").isdigit() else None
            out[r["domain"].lower()] = (v, n)
    return out


def rating_points(v, n):
    """Points for a star rating, weighted by how many reviews support it."""
    if n is None:                       # a rating with no count: weight it as a handful
        n = 5
    wr = (n * v + RATING_PRIOR_N * RATING_PRIOR_MEAN) / (n + RATING_PRIOR_N)
    pts = max(0.0, (wr - 4.0) * 20.0)   # 4.0 -> 0, 5.0 -> 20
    pts += min(9.0, 3.0 * math.log10(n)) if n >= 10 else 0.0
    return pts, wr


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


def score(row, hunts, exports, followers, ratings):
    em = row["email"].strip().lower()
    dom = em.split("@", 1)[1] if "@" in em else ""
    h = hunts.get(em) or {}
    x = exports.get(em) or {}
    s, why = 0.0, []

    # The rating is keyed by the SITE's domain; for a freemail row that is only
    # known from the hunt row, so try that first and fall back to the address.
    for d in {(h.get("domain") or "").lower().removeprefix("www."), dom}:
        if d and d in ratings:
            v, n = ratings[d]
            pts, wr = rating_points(v, n)
            s += pts
            why.append(f"{v:.1f} stars" + (f" / {n:,} reviews" if n else "") + f" (weighted {wr:.2f})")
            break

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


# A "great" lead, for runway purposes: score >= GREAT. Twenty is what a US
# practice earns from a published location (6), an address on its own domain
# (10) and its own pages showing a couple of peptide or GLP-1 terms (4) -- the
# floor Jonathan's "great leads" is taken to mean. Ratings, followers and named
# compounds all sit above it.
GREAT = 20.0
RUNWAY_TARGET_DAYS = 90   # Jonathan, Sep 23: "3 months of run time worth of great leads"


def runway(scored):
    """Days of sending the eligible great leads cover at today's daily cap.

    Counts only what serve_send would actually send -- not yet contacted, past
    every exclusion gate, inside the current AUDIENCE_ONLY focus -- so the figure
    cannot be flattered by rows the sender would skip.
    """
    sys.path.insert(0, str(ROOT / "outreach"))
    import serve_send as ss
    eligible = {r["email"].strip().lower() for r in ss.initial_candidates(ss.load_sent())}
    great = sum(1 for t in scored if t[3]["email"].strip().lower() in eligible and t[4] >= GREAT)
    cap = ss.DAILY_CAP
    days = great / cap if cap else 0
    need = max(0, RUNWAY_TARGET_DAYS * cap - great)
    focus = f" ({ss.AUDIENCE_ONLY} only)" if ss.AUDIENCE_ONLY else ""
    print(f"\nRUNWAY: {great} great leads eligible{focus} = {days:.0f} days at {cap}/day; "
          f"target {RUNWAY_TARGET_DAYS} days needs {RUNWAY_TARGET_DAYS * cap} "
          f"-> {'MET' if not need else f'{need} short'}")
    return great, days, need


def main():
    apply = "--apply" in sys.argv
    rows = list(csv.DictReader(open(QUEUE, newline="", encoding="utf-8")))
    hunts = load("scraper/hunts/*.csv", "email")
    exports = load("exports/us_peptide_vendors_*.csv", "primary_email")
    followers = followers_by_email()
    ratings = load_ratings()
    print(f"{len(rows)} queue rows; evidence: {len(hunts)} hunt, {len(exports)} export, "
          f"{len(followers)} follower counts, {len(ratings)} star ratings")

    scored = []
    for i, r in enumerate(rows):
        sc, why = score(r, hunts, exports, followers, ratings)
        # med spas first as a block, then vendors; score orders within each.
        scored.append(((0 if r["audience"] == "medspa" else 1), -sc, i, r, sc, why))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))

    print("\nTOP 12:")
    for _, _, _, r, sc, why in scored[:12]:
        print(f"  {sc:5.1f}  {r['audience']:6s} {r['business_name'][:30]:32s} {r['email'][:36]:38s} {why}")
    print("\nBOTTOM 6:")
    for _, _, _, r, sc, why in scored[-6:]:
        print(f"  {sc:5.1f}  {r['audience']:6s} {r['business_name'][:30]:32s} {r['email'][:36]:38s} {why}")

    runway(scored)
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
