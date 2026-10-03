"""One command for the daily lead hunt, when a search key is configured.

    python3 scraper/hunt_run.py --target 300 --audience medspa
    python3 scraper/hunt_run.py --target 400            # both audiences

Pulls queries from hunt_plan, searches through whichever provider .env has,
crawls and qualifies with lead_hunt, and writes a resolved CSV that
outreach/ingest_resolved.py can append to the queue. It keeps pulling query
blocks until it has enough qualified leads or runs out of plan, so "target" is
a real target rather than a fixed number of searches.

With no key in .env this exits immediately and says so: the fallback is the
agent-driven path in the daily runbook, which uses the WebSearch tool instead.
"""
import argparse, csv, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import hunt_plan, lead_hunt, search_api


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=300)
    ap.add_argument("--audience", choices=["medspa", "vendor", "both"], default="both")
    ap.add_argument("--out", default="")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--max-queries", type=int, default=400)
    a = ap.parse_args()

    p = search_api.provider()
    if not p:
        print("no search provider configured -- add SERPER_API_KEY (or BRAVE_SEARCH_API_KEY, "
              "or GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX) to .env.")
        print("until then the daily runbook's WebSearch path is the way to discover leads.")
        return 2
    print(f"search provider: {p}  target: {a.target} qualified  audience: {a.audience}")

    out = Path(a.out or (ROOT / "scraper" / "hunts" /
                         f"hunt_{time.strftime('%Y-%m-%d')}_{a.audience}.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / "scraper" / "hunts" / ".candidates.txt"

    qualified, used, seen = [], 0, set()
    plan = hunt_plan.plan()
    cursor = hunt_plan.cursor()
    while len(qualified) < a.target and used < a.max_queries and cursor < len(plan):
        block = plan[cursor:cursor + 20]
        cursor += len(block)
        cands = []
        for aud, q in block:
            if a.audience != "both" and aud != a.audience:
                continue
            used += 1
            try:
                hits = search_api.search(q, 10)
            except Exception as e:
                print(f"  search failed ({type(e).__name__}) -- stopping this block")
                break
            for title, url in hits:
                d = lead_hunt.domain_of(url)
                if d and d not in seen:
                    seen.add(d)
                    cands.append(f"{title.split('|')[0].strip()[:60]}|{d}")
        if not cands:
            continue
        tmp.write_text("\n".join(cands), encoding="utf-8")
        part = out.with_suffix(f".part{used}.csv")
        lead_hunt.main(str(tmp), str(part), a.workers)
        with open(part, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        qualified += [r for r in rows if r["status"] == "ok"]
        part.unlink(missing_ok=True)
        print(f"  queries used {used} | qualified so far {len(qualified)}")
        hunt_plan.CURSOR.write_text(str(cursor))

    if qualified:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=lead_hunt.COLS)
            w.writeheader()
            w.writerows(qualified)
    print(f"\ndone: {len(qualified)} qualified from {used} queries -> {out}")
    if len(qualified) < a.target:
        print(f"short of target by {a.target - len(qualified)}. The usual causes are a thin "
              f"query plan (see hunt_plan stats) and clinics that publish a contact form "
              f"instead of an address.")
    print(f"next: RESOLVED_DIR={out.parent} RESOLVED_FILES={out.name} "
          f"python3 outreach/ingest_resolved.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
