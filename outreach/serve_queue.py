"""Standalone, dependency-free server for the outreach draft queue.

This is a self-contained companion to emailer/draft_queue.py that uses ONLY the
Python standard library, so the paced drafting loop can keep running under plain
`python3` even if the project virtualenv is not present (e.g. after a fresh
container/clone). It reads the already-rendered queue and the contact logs that
are committed to the repo, so no .env, config, or template rendering is needed
at wave time.

    python3 outreach/serve_queue.py stats
    python3 outreach/serve_queue.py next 40 [batch.json]   # print table + write batch json
    python3 outreach/serve_queue.py record batch.json       # mark a batch's recipients drafted

The queue (outreach/draft_queue.csv) is built by emailer/draft_queue.py --build.
Dedup sources: outreach/sent_log.csv, outreach/drafted_log.csv, outreach/drafted_seed.txt.
"""
import csv, json, re, sys
from pathlib import Path

csv.field_size_limit(10_000_000)
OUT = Path(__file__).resolve().parent
QUEUE = OUT / "draft_queue.csv"
SENT = OUT / "sent_log.csv"
DRAFTED = OUT / "drafted_log.csv"
SEED = OUT / "drafted_seed.txt"


def done_set():
    done = set()
    for f in (SENT, DRAFTED):
        if f.exists():
            with open(f, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    e = (r.get("email") or "").strip().lower()
                    if not e:
                        continue
                    done.add(e)
                    done.add(r.get("domain") or e.split("@", 1)[1])
    if SEED.exists():
        for e in SEED.read_text().split():
            if "@" in e:
                e = e.strip().lower()
                done.add(e); done.add(e.split("@", 1)[1])
    return done


def pending_rows():
    done = done_set()
    with open(QUEUE, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [r for r in rows if r["email"].strip().lower() not in done]


def interleave(pend, n):
    ms = [r for r in pend if r["audience"] == "medspa"]
    vn = [r for r in pend if r["audience"] == "vendor"]
    out, i, j = [], 0, 0
    while len(out) < n and (i < len(ms) or j < len(vn)):
        if i < len(ms):
            out.append(ms[i]); i += 1
        if len(out) < n and j < len(vn):
            out.append(vn[j]); j += 1
    return out[:n]


def cmd_stats():
    tot = 0
    if QUEUE.exists():
        with open(QUEUE, newline="", encoding="utf-8") as fh:
            tot = sum(1 for _ in csv.DictReader(fh))
    print(f"queue={tot} pending={len(pending_rows())} done={len(done_set())}")


def cmd_next(n, out_json):
    batch = interleave(pending_rows(), n)
    Path(out_json).write_text(json.dumps(
        [{"audience": r["audience"], "to": r["to"], "subject": r["subject"], "body": r["body"]} for r in batch]))
    from collections import Counter
    print(f"BATCH {len(batch)} {dict(Counter(r['audience'] for r in batch))}")
    bad = [r["to"] for r in batch if "{" in r["subject"] or "{" in r["body"]]
    if bad:
        print("!!! PLACEHOLDER LEFTOVERS:", bad)
    for i, r in enumerate(batch, 1):
        name = r["subject"].split(" - US-made")[0]
        if r["audience"] == "medspa":
            m = re.search(r"and saw you offer (.*?) -- that's exactly", r["body"], re.S)
            peps = m.group(1).replace("\n", " ") if m else "??"
            print(f'{i}\tMS\t{r["to"]}\t{name}\t{peps}')
        else:
            print(f'{i}\tVN\t{r["to"]}\t{name}\t-')


def cmd_record(batch_json):
    emails = [x["to"].strip().lower() for x in json.loads(Path(batch_json).read_text()) if "@" in x.get("to", "")]
    new = not DRAFTED.exists()
    with open(DRAFTED, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["email", "domain", "mode"])
        for e in emails:
            w.writerow([e, e.split("@", 1)[1], "draft"])
    print(f"recorded {len(emails)} drafted")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "stats":
        cmd_stats()
    elif a[0] == "next":
        cmd_next(int(a[1]) if len(a) > 1 else 40, a[2] if len(a) > 2 else str(OUT.parent / ".next_batch.json"))
    elif a[0] == "record":
        cmd_record(a[1])
    else:
        sys.exit("usage: serve_queue.py [stats | next N [out.json] | record batch.json]")
