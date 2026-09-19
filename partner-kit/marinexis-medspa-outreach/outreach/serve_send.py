"""Dependency-free send engine for the Marinexis med spa campaign.

Standard library only, so it runs under plain `python3` in any fresh Claude Code
container. All state lives in CSVs committed to the repo, which is what keeps the
campaign honest across sessions, machines and restarts.

    python3 outreach/serve_send.py stats
    python3 outreach/serve_send.py next 10 wave.json     # build the next wave
    python3 outreach/serve_send.py record wave.json 10   # mark rows 1..10 as sent
    python3 outreach/serve_send.py record wave.json 10 --skip 3,7
    python3 outreach/serve_send.py mark replied addrs.txt
    python3 outreach/serve_send.py export                # refresh the category CSVs

State files (all under outreach/, all committed):
  medspa_queue.csv    every verified med spa not yet contacted (contact data only)
  sent_log.csv        one row per med spa emailed: when, how many follow-ups, status
  do_not_contact.csv  addresses/domains this account must never email

Email bodies are rendered at send time from emailer/message_medspa.txt and
emailer/followup_medspa.txt, so editing the pitch never means rebuilding a queue.

Cadence: a follow-up goes out FOLLOWUP_DAYS after the last touch, at most
MAX_FOLLOWUPS times, and never to anyone whose status is not "active".
DAILY_CAP and HOURLY_CAP keep the account under Gmail's sending limits.
"""
import csv, json, os, re, sys, textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

csv.field_size_limit(10_000_000)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outreach"
QUEUE = OUT / "medspa_queue.csv"
SENT = OUT / "sent_log.csv"
DNC = OUT / "do_not_contact.csv"
INITIAL_TPL = ROOT / "emailer" / "message_medspa.txt"
FU_TPL = ROOT / "emailer" / "followup_medspa.txt"


def _dotenv():
    """Values from .env, so settings live in one place the user actually edits."""
    d = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    d[k.strip()] = v
    return d


ENV = _dotenv()


def cfg(key, default):
    """Real environment wins, then .env, then the built-in default."""
    return os.environ.get(key) or ENV.get(key) or default


# --- pacing -----------------------------------------------------------------
# A brand-new mailbox should warm up: start at 20/day for the first week, then
# 50, then 100. Raise DAILY_CAP in .env once the account has history. The
# default is the warm-up number on purpose -- someone who never writes a .env
# should send too little, not too much. 100 is the ceiling; above that Gmail
# throttles and the domain is slow to recover.
DAILY_CAP = int(cfg("DAILY_CAP", "20"))
HOURLY_CAP = int(cfg("HOURLY_CAP", "10"))
FOLLOWUP_DAYS = int(cfg("FOLLOWUP_DAYS", "3"))
MAX_FOLLOWUPS = int(cfg("MAX_FOLLOWUPS", "3"))
FU_SHARE = float(cfg("FU_SHARE", "0.5"))                # at most this fraction of a wave is follow-ups
LOCAL_TZ = cfg("LOCAL_TZ", "America/Los_Angeles")
WRAP_AT = 78

# --- quality filters ---------------------------------------------------------
# The pitch is US-made supply with no customs risk, so non-US practices are skipped.
FOREIGN = re.compile(
    r"\.(ca|uk|co\.uk|is|cn|ae|eu|au|de|fr|in|mx|nl|ru|pl|es|it|br|hk|sg|nz|ie|ch|se|no|dk|fi|tw|jp|kr)$"
    r"|costarica|french|german|british|canad|austral|europe|\buae\b|-uk\b|\buk-|uk\.(com|net|org)$", re.I)
FOREIGN_NAME = re.compile(
    r"\b(uae|dubai|uk|london|centre|toronto|vancouver|sydney|melbourne|wuhan|shanghai|shenzhen|beijing"
    r"|hong kong|gmbh|ltd|pty|canada|europe|costa rica|australia|india|china)\b", re.I)
FOREIGN_LOCAL = {"contato", "kontakt", "contacto", "info-de", "info-uk"}
# scraped page titles that are not a business name -> fall back to the bare domain
JUNK_NAME = re.compile(
    r"click here|^visit\b|\bpromo\b|\beligible\b|\beditor\b|\bnotes\b|\balternative\b|\bdosing\b|cheapest"
    r"|view source|^source$|^usa$|^peptides?$|^buy\b|for sale|coupon|discount|\boffers?\b|→|↗"
    r"|^(home|welcome|shop|store|login|account|cart|menu|search|about|contact|services|book|blog"
    r"|best|top|new|free|quality|premium|official|trusted|verified|med spa|medspa|wellness)$", re.I)
FREEMAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "proton.me", "protonmail.com", "pm.me",
            "icloud.com", "aol.com", "live.com", "msn.com", "comcast.net", "att.net"}

STAGE_LINES = {
    1: "Just floating this back to the top of your inbox.",
    2: "Checking in once more in case this got buried.",
    3: "Last note from me on this -- I don't want to clutter your inbox.",
}
COLS = ["email", "domain", "audience", "mode", "via", "sent_at", "last_touch_at", "stage", "status"]

# Company facts are fixed; the sender identity comes from .env so each teammate
# sends as themselves.
COMPANY = {
    "SENDER_COMPANY": "Marinexis Biologics",
    "SENDER_POSTAL_ADDRESS": "6671 S Las Vegas Blvd, Las Vegas, NV 89118",
}
DEFAULT_SENDER = {"SENDER_NAME": "", "SENDER_EMAIL": "", **COMPANY}
UNSUB_LINE = 'Don\'t want to hear from us again? Reply with "unsubscribe" and we\'ll remove you.'


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sender():
    """Sender identity from .env; company facts are not overridable by accident."""
    d = dict(DEFAULT_SENDER)
    for k in ("SENDER_NAME", "SENDER_EMAIL"):
        v = cfg(k, "")
        if v:
            d[k] = v
    if not d["SENDER_NAME"] or not d["SENDER_EMAIL"]:
        sys.exit("ERROR: set SENDER_NAME and SENDER_EMAIL in .env before sending (copy .env.example to .env).")
    return d


# ---------- state ------------------------------------------------------------
def load_sent():
    rows = []
    if SENT.exists():
        with open(SENT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({k: (r.get(k) or "").strip() for k in COLS})
    for r in rows:
        r["email"] = r["email"].lower()
        r["domain"] = r["domain"] or r["email"].split("@", 1)[1]
        r["audience"] = r["audience"] or "medspa"
        r["mode"] = r["mode"] or "sent"
        r["last_touch_at"] = r["last_touch_at"] or r["sent_at"]
        r["stage"] = r["stage"] or "0"
        r["status"] = r["status"] or "active"
    return rows


def save_sent(rows):
    with open(SENT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def load_queue():
    if not QUEUE.exists():
        return []
    with open(QUEUE, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("email") or "").strip()]


def load_dnc():
    keys = set()
    if DNC.exists():
        with open(DNC, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                e = (r.get("email") or "").strip().lower()
                d = (r.get("domain") or "").strip().lower()
                if e:
                    keys.add(e)
                if d:
                    keys.add(d)
    return keys


def local_day(ts_iso):
    """Calendar day in the sender's own timezone, so the daily cap resets at local midnight."""
    try:
        from zoneinfo import ZoneInfo
        return parse(ts_iso).astimezone(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%d")
    except Exception:
        return parse(ts_iso).strftime("%Y-%m-%d")


def today_count(rows):
    t = local_day(iso(now()))
    return sum(1 for r in rows if r["last_touch_at"] and local_day(r["last_touch_at"]) == t)


# ---------- rendering --------------------------------------------------------
def clean_name(name, domain):
    n = (name or "").strip()
    if not n or len(n) < 3 or len(n.split()) > 6 or JUNK_NAME.search(n):
        return domain
    return n


def is_foreign(domain, name=""):
    return bool(FOREIGN.search(domain) or FOREIGN_NAME.search(name or ""))


def split_template(path):
    """A template file is 'Subject: ...' then a blank line then the body."""
    text = path.read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    subject = first.split(":", 1)[1].strip() if first.lower().startswith("subject:") else ""
    return subject, rest.lstrip("\n").rstrip("\n")


def fill(text, subs):
    for k, v in subs.items():
        text = text.replace(k, v)
    return text


def rewrap(body):
    """Re-wrap prose that a long business name or peptide list pushed past the margin.

    Only paragraphs above the wrap column are touched, and only above the CAN-SPAM
    footer, so the signature and the footer stay exactly as written.
    """
    head, sep, foot = body.partition("\n--\n")
    blocks = []
    for block in head.split("\n\n"):
        lines = block.split("\n")
        if max((len(l) for l in lines), default=0) > WRAP_AT:
            block = "\n".join(textwrap.wrap(" ".join(l.strip() for l in lines),
                                            width=WRAP_AT, break_long_words=False,
                                            break_on_hyphens=False))
        blocks.append(block)
    return "\n\n".join(blocks) + sep + foot


def orig_subject(name):
    tail = "US-made peptide supply for your practice"
    return tail if name in ("your practice",) else f"{name} - {tail}"


def base_subs(name, peps, sd):
    return {
        "{business_name}": name, "{peptides}": peps,
        "{sender_name}": sd["SENDER_NAME"], "{sender_email}": sd["SENDER_EMAIL"],
        "{sender_company}": sd["SENDER_COMPANY"], "{sender_postal_address}": sd["SENDER_POSTAL_ADDRESS"],
        "{unsubscribe_line}": UNSUB_LINE,
    }


def render_initial(name, peps, sd):
    subj_t, body_t = split_template(INITIAL_TPL)
    subs = base_subs(name, peps, sd)
    return fill(subj_t, subs), rewrap(fill(body_t, subs))


def render_followup(name, peps, stage, sd):
    subj_t, body_t = split_template(FU_TPL)
    subs = base_subs(name, peps, sd)
    subs["{orig_subject}"] = orig_subject(name)
    subs["{stage_line}"] = STAGE_LINES.get(stage, STAGE_LINES[max(STAGE_LINES)])
    return fill(subj_t, subs), rewrap(fill(body_t, subs))


# ---------- selection --------------------------------------------------------
def initial_candidates(sent_rows):
    blocked = load_dnc()
    for r in sent_rows:
        blocked.add(r["email"])
        blocked.add(r["domain"])
    out, seen = [], set()
    for r in load_queue():
        e = r["email"].strip().lower()
        if "@" not in e:
            continue
        d = e.split("@", 1)[1]
        if e in blocked or d in blocked or d in seen:
            continue
        if is_foreign(d, r.get("business_name", "")):
            continue
        if e.split("@", 1)[0] in FOREIGN_LOCAL:
            continue
        seen.add(d)
        out.append(r)
    return out


def due_followups(sent_rows):
    cutoff = now() - timedelta(days=FOLLOWUP_DAYS)
    due = [r for r in sent_rows
           if r["status"] == "active" and int(r["stage"]) < MAX_FOLLOWUPS and parse(r["last_touch_at"]) <= cutoff]
    due.sort(key=lambda r: r["last_touch_at"])
    return due


def display_name(raw, domain):
    name = clean_name(raw, domain)
    if name == domain and domain in FREEMAIL:      # a freemail domain is not a business name
        name = "your practice"
    return name


# ---------- commands ---------------------------------------------------------
def cmd_next(n, out_json):
    rows = load_sent()
    sd = sender()
    qi = {r["email"].strip().lower(): r for r in load_queue()}
    budget = max(0, DAILY_CAP - today_count(rows))
    n = min(n, budget, HOURLY_CAP)
    items = []

    fu_cap = int(n * FU_SHARE + 0.999) if n else 0
    for r in due_followups(rows):
        if len(items) >= fu_cap:
            break
        q = qi.get(r["email"], {})
        name = display_name(q.get("business_name", ""), r["domain"])
        peps = (q.get("peptides") or "peptides").strip()
        stage = int(r["stage"]) + 1
        subj, body = render_followup(name, peps, stage, sd)
        items.append({"kind": "fu", "stage": stage, "audience": "medspa", "to": r["email"], "name": name,
                      "peps": peps, "city": q.get("city", ""), "state": q.get("state", ""),
                      "subject": subj, "body": body, "draftId": ""})

    for r in initial_candidates(rows):
        if len(items) >= n:
            break
        e = r["email"].strip().lower()
        name = display_name(r.get("business_name", ""), e.split("@", 1)[1])
        peps = (r.get("peptides") or "peptides").strip()
        subj, body = render_initial(name, peps, sd)
        items.append({"kind": "initial", "stage": 0, "audience": "medspa", "to": e, "name": name,
                      "peps": peps, "city": r.get("city", ""), "state": r.get("state", ""),
                      "subject": subj, "body": body, "draftId": ""})

    Path(out_json).write_text(json.dumps(items, indent=1), encoding="utf-8")
    fu = sum(1 for i in items if i["kind"] == "fu")
    print(f"BATCH {len(items)}  fu={fu} initial={len(items) - fu}  (budget_left_today={budget}, cap={DAILY_CAP})")
    bad = [i["to"] for i in items if "{" in i["subject"] or "{" in i["body"]]
    if bad:
        print("!!! PLACEHOLDER LEFTOVERS:", bad)
    for i, it in enumerate(items, 1):
        kind = f"FU{it['stage']}" if it["kind"] == "fu" else "INIT"
        where = f"{it['city']}, {it['state']}".strip(", ") or "-"
        print(f"{i}\t{kind}\t{it['to']}\t{it['name']}\t{where}\t{it['peps'][:48]}")


def cmd_record(batch_json, upto=None, skip=()):
    items = json.loads(Path(batch_json).read_text(encoding="utf-8"))
    rows = load_sent()
    idx = {r["email"]: r for r in rows}
    t = iso(now())
    via = sender()["SENDER_EMAIL"]
    n_new = n_fu = 0
    for i, it in enumerate(items, 1):
        if upto and i > upto:
            break
        if i in skip:
            continue
        e = it["to"].strip().lower()
        if it["kind"] == "initial":
            if e in idx:
                continue                                   # idempotent
            row = {"email": e, "domain": e.split("@", 1)[1], "audience": "medspa", "mode": "sent", "via": via,
                   "sent_at": t, "last_touch_at": t, "stage": "0", "status": "active"}
            rows.append(row)
            idx[e] = row
            n_new += 1
        else:
            r = idx.get(e)
            if r and int(r["stage"]) < int(it["stage"]):
                r["stage"] = str(it["stage"])
                r["last_touch_at"] = t
                n_fu += 1
    save_sent(rows)
    print(f"recorded initial={n_new} followups={n_fu}  (rows 1..{upto or len(items)}, skipped {sorted(skip) or 'none'})")


def cmd_mark(status, path):
    assert status in ("replied", "bounced", "unsubscribed", "active"), status
    keys = {k.strip().lower().lstrip("<").rstrip(">")
            for k in Path(path).read_text(encoding="utf-8").split() if k.strip()}
    rows = load_sent()
    n = 0
    for r in rows:
        if (r["email"] in keys or r["domain"] in keys) and r["status"] != status:
            r["status"] = status
            n += 1
    save_sent(rows)
    if status in ("unsubscribed", "bounced"):                # never touch these again, from any list
        seen = load_dnc()
        add = [k for k in keys if k not in seen]
        if add:
            new = not DNC.exists()
            with open(DNC, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["email", "domain", "reason", "status"])
                if new:
                    w.writeheader()
                for k in add:
                    w.writerow({"email": k if "@" in k else "", "domain": k.split("@")[-1],
                                "reason": status, "status": status})
            print(f"added {len(add)} to do_not_contact.csv")
    print(f"marked {n} as {status}")


def cmd_export():
    rows = load_sent()

    def dump(name, sel, cols):
        with open(OUT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in sel:
                w.writerow({k: r.get(k, "") for k in cols})
        return len(sel)

    base = ["email", "domain", "audience", "sent_at", "last_touch_at", "stage", "status"]
    n1 = dump("contacted.csv", [r for r in rows if r["status"] in ("active", "manual")], base)
    n2 = dump("replied.csv", [r for r in rows if r["status"] == "replied"], base)
    n3 = dump("bounced.csv", [r for r in rows if r["status"] == "bounced"], base)
    pend = initial_candidates(rows)
    n4 = dump("not_yet_emailed.csv",
              [{"email": r["email"], "domain": r["email"].split("@", 1)[1],
                "business_name": r.get("business_name", ""), "city": r.get("city", ""),
                "state": r.get("state", "")} for r in pend],
              ["email", "domain", "business_name", "city", "state"])
    print(f"exported contacted={n1} replied={n2} bounced={n3} not_yet_emailed={n4}")


def cmd_stats():
    from collections import Counter
    rows = load_sent()
    st = Counter(r["status"] for r in rows)
    sg = Counter(r["stage"] for r in rows if r["status"] == "active")
    pend = len(initial_candidates(rows))
    due = len(due_followups(rows))
    tc = today_count(rows)
    active = [r for r in rows if r["status"] == "active" and int(r["stage"]) < MAX_FOLLOWUPS]
    nxt = min((parse(r["last_touch_at"]) + timedelta(days=FOLLOWUP_DAYS) for r in active), default=None)
    print(f"sent_total={len(rows)} status={dict(st)} active_by_stage={dict(sorted(sg.items()))}")
    print(f"today_sent={tc} budget_left_today={max(0, DAILY_CAP - tc)} cap={DAILY_CAP}")
    print(f"queue_pending={pend} followups_due_now={due} next_followup_due={iso(nxt) if nxt else '-'}")
    print("campaign=" + ("complete" if (pend == 0 and not active) else "running"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "stats":
        cmd_stats()
    elif a[0] == "next":
        cmd_next(int(a[1]) if len(a) > 1 else 10, a[2] if len(a) > 2 else str(ROOT / ".wave.json"))
    elif a[0] == "record":
        upto, skip, rest, i = None, set(), a[2:], 0
        while i < len(rest):
            if rest[i] == "--skip":
                skip = {int(x) for x in rest[i + 1].split(",") if x.strip()}
                i += 2
            else:
                upto = int(rest[i])
                i += 1
        cmd_record(a[1], upto, skip)
    elif a[0] == "mark":
        cmd_mark(a[1], a[2])
    elif a[0] == "export":
        cmd_export()
    else:
        sys.exit("usage: serve_send.py [stats | next N out.json | record batch.json [UPTO] [--skip i,j] "
                 "| mark replied|bounced|unsubscribed|active file | export]")
