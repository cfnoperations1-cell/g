"""Standalone, dependency-free send engine for the outreach campaign.

Drives real sends (initial outreach + timed follow-ups) with durable tracking, using
ONLY the Python standard library so the hourly loop runs under plain `python3`
after any container/clone restart. All state lives in files committed to the repo.

    python3 outreach/serve_send.py stats
    python3 outreach/serve_send.py migrate                    # upgrade sent_log.csv schema in place
    python3 outreach/serve_send.py next 100 batch.json        # build the next wave (follow-ups due first,
                                                              #   then initial sends, vendors before med spas)
    python3 outreach/serve_send.py record batch.json [UPTO] [--skip 3,7]
                                                              # mark rows 1..UPTO of the batch as sent
    python3 outreach/serve_send.py mark replied|bounced|unsubscribed addrs.txt
                                                              # stop follow-ups for these emails/domains

State files (all under outreach/, all committed):
  draft_queue.csv  - every verified contact with its rendered initial subject/body (built by
                     emailer/draft_queue.py --build)
  sent_log.csv     - one row per contact we have emailed: when, how many follow-ups, status
  draft_ids.csv    - optional email -> Gmail draft id for contacts that already have a
                     verified draft; those are sent by draft id (which also clears the draft)

Follow-up cadence: FOLLOWUP_DAYS after the last touch, up to MAX_FOLLOWUPS per contact, and
never to anyone whose status is not "active" (replied / bounced / unsubscribed).
DAILY_CAP guards Google Workspace's daily sending limit; the wave size is clipped to it.
"""
import csv, json, os, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

csv.field_size_limit(10_000_000)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outreach"
QUEUE = OUT / "draft_queue.csv"
SENT = OUT / "sent_log.csv"
DRAFT_IDS = OUT / "draft_ids.csv"
FU_TPL = {"medspa": ROOT / "emailer" / "followup_medspa.txt", "vendor": ROOT / "emailer" / "followup_vendor.txt"}

DAILY_CAP = int(os.environ.get("DAILY_CAP", "1900"))     # Google Workspace hard limit is 2,000/day
FOLLOWUP_DAYS = int(os.environ.get("FOLLOWUP_DAYS", "3"))
MAX_FOLLOWUPS = int(os.environ.get("MAX_FOLLOWUPS", "3"))
PRIORITY_DOMAINS = ["heritagelabsusa.com"]                 # "peptide veterans": the one veteran-owned vendor
LEGACY_SENT_AT = "2026-09-14T16:00:00Z"                    # all pre-schema sends went out on 2026-09-14

COLS = ["email", "domain", "audience", "mode", "via", "sent_at", "last_touch_at", "stage", "status"]
STAGE_LINES = {
    1: "Just floating this back to the top of your inbox.",
    2: "Checking in once more in case this got buried.",
    3: "Last note from me on this -- I don't want to clutter your inbox.",
}
DEFAULT_SENDER = {
    "SENDER_NAME": "Jonathan Cole",
    "SENDER_EMAIL": "jonathan@marinexisbiologics.com",
    "SENDER_COMPANY": "Marinexis Biologics",
    "SENDER_POSTAL_ADDRESS": "6671 S Las Vegas Blvd, Las Vegas, NV 89118",
}
UNSUB_LINE = "Don't want to hear from us again? Reply with \"unsubscribe\" and we'll remove you."


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sender():
    d = dict(DEFAULT_SENDER)
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in d and v:
                    d[k] = v
    return d


# ---------- state ----------
def load_sent():
    rows = []
    if SENT.exists():
        with open(SENT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({k: (r.get(k) or "").strip() for k in COLS})
    for r in rows:                                   # tolerate the pre-follow-up schema
        r["email"] = r["email"].lower()
        r["domain"] = r["domain"] or r["email"].split("@", 1)[1]
        r["mode"] = r["mode"] or "sent"
        r["sent_at"] = r["sent_at"] or LEGACY_SENT_AT
        r["last_touch_at"] = r["last_touch_at"] or r["sent_at"]
        r["stage"] = r["stage"] or "0"
        r["status"] = r["status"] or "active"
    return rows


def save_sent(rows):
    with open(SENT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader(); w.writerows(rows)


def load_queue():
    with open(QUEUE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_draft_ids():
    m = {}
    if DRAFT_IDS.exists():
        with open(DRAFT_IDS, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                m[r["email"].strip().lower()] = r["draft_id"].strip()
    return m


def today_count(rows):
    t = iso(now())[:10]
    return sum(1 for r in rows if r["last_touch_at"][:10] == t)


# ---------- rendering ----------
def peps_of(qrow):
    if qrow and qrow.get("audience") == "medspa":
        m = re.search(r"and saw you offer (.*?) -- that's exactly", qrow["body"], re.S)
        if m:
            return m.group(1).replace("\n", " ")
    return "peptides"


def orig_subject(aud, name):
    return f"{name} - US-made peptide supply for your practice" if aud == "medspa" else f"{name} - US-made peptide supply, wholesale"


def render_fu(aud, name, peps, stage, sd):
    text = FU_TPL[aud].read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    subject_t = first.split(":", 1)[1].strip() if first.lower().startswith("subject:") else "Re: {orig_subject}"
    body_t = rest.lstrip("\n").rstrip("\n")
    subs = {
        "{orig_subject}": orig_subject(aud, name), "{business_name}": name, "{peptides}": peps,
        "{stage_line}": STAGE_LINES.get(stage, STAGE_LINES[max(STAGE_LINES)]),
        "{sender_email}": sd["SENDER_EMAIL"], "{sender_company}": sd["SENDER_COMPANY"],
        "{sender_postal_address}": sd["SENDER_POSTAL_ADDRESS"], "{unsubscribe_line}": UNSUB_LINE,
        "{sender_name}": sd["SENDER_NAME"],
    }
    for k, v in subs.items():
        subject_t = subject_t.replace(k, v); body_t = body_t.replace(k, v)
    return subject_t, body_t


# ---------- selection ----------
def initial_candidates(sent_rows):
    done = set()
    for r in sent_rows:
        done.add(r["email"]); done.add(r["domain"])
    cands = []
    for r in load_queue():
        e = r["email"].strip().lower()
        d = e.split("@", 1)[1]
        if e in done or d in done:
            continue
        cands.append(r)
    vendors = [r for r in cands if r["audience"] == "vendor"]
    medspas = [r for r in cands if r["audience"] == "medspa"]
    ordered = vendors + medspas                                   # vendors first, then med spas
    pri = [r for r in ordered if r["email"].split("@", 1)[1] in PRIORITY_DOMAINS]
    rest = [r for r in ordered if r not in pri]
    out, seen = [], set()
    for r in pri + rest:                                          # one contact per domain per campaign
        d = r["email"].lower().split("@", 1)[1]
        if d in seen:
            continue
        seen.add(d); out.append(r)
    return out


def due_followups(sent_rows):
    cutoff = now() - timedelta(days=FOLLOWUP_DAYS)
    due = [r for r in sent_rows
           if r["status"] == "active" and int(r["stage"]) < MAX_FOLLOWUPS and parse(r["last_touch_at"]) <= cutoff]
    due.sort(key=lambda r: r["last_touch_at"])
    return due


# ---------- commands ----------
def cmd_next(n, out_json):
    rows = load_sent(); qi = {r["email"].strip().lower(): r for r in load_queue()}
    dids = load_draft_ids(); sd = sender()
    budget = max(0, DAILY_CAP - today_count(rows)); n = min(n, budget)
    items = []
    for r in due_followups(rows):
        if len(items) >= n:
            break
        qrow = qi.get(r["email"]); aud = r["audience"] if r["audience"] in FU_TPL else "vendor"
        name = (qrow or {}).get("business_name") or r["domain"]; peps = peps_of(qrow); stage = int(r["stage"]) + 1
        subj, body = render_fu(aud, name, peps, stage, sd)
        items.append({"kind": "fu", "stage": stage, "audience": aud, "to": r["email"], "name": name,
                      "peps": peps if aud == "medspa" else "-", "subject": subj, "body": body, "draftId": ""})
    for r in initial_candidates(rows):
        if len(items) >= n:
            break
        e = r["email"].strip().lower()
        items.append({"kind": "initial", "stage": 0, "audience": r["audience"], "to": e, "name": r["business_name"],
                      "peps": peps_of(r) if r["audience"] == "medspa" else "-", "subject": r["subject"],
                      "body": r["body"], "draftId": dids.get(e, "")})
    Path(out_json).write_text(json.dumps(items), encoding="utf-8")
    fu = sum(1 for i in items if i["kind"] == "fu"); ini = len(items) - fu
    print(f"BATCH {len(items)}  fu={fu} initial={ini}  (budget_left_today={budget}, cap={DAILY_CAP})")
    bad = [i["to"] for i in items if "{" in i["subject"] or "{" in i["body"]]
    if bad:
        print("!!! PLACEHOLDER LEFTOVERS:", bad)
    for i, it in enumerate(items, 1):
        kind = f"FU{it['stage']}" if it["kind"] == "fu" else "INIT"
        aud = "MS" if it["audience"] == "medspa" else "VN"
        print(f"{i}\t{kind}\t{aud}\t{it['to']}\t{it['name']}\t{it['peps']}\t{it['draftId'] or '-'}")


def cmd_record(batch_json, upto=None, skip=()):
    items = json.loads(Path(batch_json).read_text(encoding="utf-8"))
    rows = load_sent(); idx = {r["email"]: r for r in rows}; t = iso(now()); via = sender()["SENDER_EMAIL"]
    n_new = n_fu = 0
    for i, it in enumerate(items, 1):
        if upto and i > upto:
            break
        if i in skip:
            continue
        e = it["to"].strip().lower()
        if it["kind"] == "initial":
            if e in idx:
                continue                                          # idempotent
            row = {"email": e, "domain": e.split("@", 1)[1], "audience": it["audience"], "mode": "sent", "via": via,
                   "sent_at": t, "last_touch_at": t, "stage": "0", "status": "active"}
            rows.append(row); idx[e] = row; n_new += 1
        else:
            r = idx.get(e)
            if r and int(r["stage"]) < int(it["stage"]):
                r["stage"] = str(it["stage"]); r["last_touch_at"] = t; n_fu += 1
    save_sent(rows)
    print(f"recorded initial={n_new} followups={n_fu}  (rows 1..{upto or len(items)}, skipped {sorted(skip) or 'none'})")


def cmd_mark(status, path):
    assert status in ("replied", "bounced", "unsubscribed", "active"), status
    keys = {k.strip().lower().lstrip("<").rstrip(">") for k in Path(path).read_text(encoding="utf-8").split() if k.strip()}
    rows = load_sent(); n = 0
    for r in rows:
        if (r["email"] in keys or r["domain"] in keys) and r["status"] != status:
            r["status"] = status; n += 1
    save_sent(rows)
    print(f"marked {n} as {status}")


def cmd_stats():
    rows = load_sent()
    from collections import Counter
    st = Counter(r["status"] for r in rows)
    sg = Counter(r["stage"] for r in rows if r["status"] == "active")
    pend = len(initial_candidates(rows)); due = len(due_followups(rows)); tc = today_count(rows)
    active = [r for r in rows if r["status"] == "active" and int(r["stage"]) < MAX_FOLLOWUPS]
    nxt = min((parse(r["last_touch_at"]) + timedelta(days=FOLLOWUP_DAYS) for r in active), default=None)
    print(f"sent_total={len(rows)} status={dict(st)} active_by_stage={dict(sorted(sg.items()))}")
    print(f"today_sent={tc} budget_left_today={max(0, DAILY_CAP - tc)} cap={DAILY_CAP}")
    print(f"initial_pending={pend} followups_due_now={due} next_followup_due={iso(nxt) if nxt else '-'}")
    print("campaign=" + ("complete" if (pend == 0 and not active) else "running"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "stats":
        cmd_stats()
    elif a[0] == "migrate":
        save_sent(load_sent()); print("migrated", SENT)
    elif a[0] == "next":
        cmd_next(int(a[1]) if len(a) > 1 else 100, a[2] if len(a) > 2 else str(OUT.parent / ".send_batch.json"))
    elif a[0] == "record":
        upto = None; skip = set(); rest = a[2:]
        i = 0
        while i < len(rest):
            if rest[i] == "--skip":
                skip = {int(x) for x in rest[i + 1].split(",") if x.strip()}; i += 2
            else:
                upto = int(rest[i]); i += 1
        cmd_record(a[1], upto, skip)
    elif a[0] == "mark":
        cmd_mark(a[1], a[2])
    else:
        sys.exit("usage: serve_send.py [stats | migrate | next N out.json | record batch.json [UPTO] [--skip i,j] | mark STATUS file]")
