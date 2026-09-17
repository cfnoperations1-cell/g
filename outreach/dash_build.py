"""Build exports/dashboard_artifact.html from the tracking CSVs.

The published dashboard used to be hand-edited every wave, which meant the reply
and bounce lists drifted from what sent_log.csv actually said. This fills
exports/dashboard_template.html from the log instead, so the page cannot
disagree with the data.

Anything the log cannot know -- whether a reply was an autoresponder, whether
the catalog has been sent back -- lives in outreach/reply_notes.csv as
email,note pairs and is merged in.

    python3 outreach/dash_build.py
"""
import csv, html, json, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "outreach"))
import serve_send as ss

TPL = ROOT / "exports" / "dashboard_template.html"
OUT = ROOT / "exports" / "dashboard_artifact.html"
NOTES = ROOT / "outreach" / "reply_notes.csv"


def notes():
    if not NOTES.exists():
        return {}
    with open(NOTES, newline="", encoding="utf-8") as f:
        return {r["email"].strip().lower(): (r.get("note") or "").strip()
                for r in csv.DictReader(f) if r.get("email")}


def read(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def day_label(iso):
    d = ss.local_day(iso)                      # YYYY-MM-DD on the Pacific day
    y, m, dd = d.split("-")
    return f"{['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m)]} {int(dd)}", d


def main():
    rows = ss.load_sent()
    note = notes()
    queue_pending = ss.initial_candidates(rows)
    st = Counter(r["status"] for r in rows)
    emailed = len(rows)
    bounced = st.get("bounced", 0)
    unsub = st.get("unsubscribed", 0)
    manual = st.get("manual", 0)
    active = st.get("active", 0)
    replied_rows = [r for r in rows if r["status"] == "replied"]
    autos = [r for r in replied_rows if "auto" in note.get(r["email"].lower(), "")]
    real = [r for r in replied_rows if r not in autos]
    catalog = [r for r in replied_rows if "catalog" in note.get(r["email"].lower(), "")]
    by_aud = Counter(r["audience"] for r in rows)
    today = ss.today_count(rows)
    stages = Counter(r["stage"] for r in rows if r["status"] == "active")

    # first-touch sends per Pacific day, vendors and med spas separately
    per_day = defaultdict(lambda: {"v": 0, "m": 0})
    for r in rows:
        if not r.get("sent_at"):
            continue
        lbl, key = day_label(r["sent_at"])
        per_day[(key, lbl)]["m" if r["audience"] == "medspa" else "v"] += 1
    days = [{"d": lbl, "v": c["v"], "m": c["m"]}
            for (key, lbl), c in sorted(per_day.items())]

    statuses = [
        {"k": "Awaiting reply", "sub": "eligible for follow-ups", "n": active, "c": "var(--accent)"},
        {"k": "Replied", "sub": f"{len(real)} real replies + {len(autos)} auto-replies · no more automated mail",
         "n": len(replied_rows), "c": "var(--done)"},
        {"k": "Bounced", "sub": "address rejected · excluded", "n": bounced, "c": "var(--block)"},
        {"k": "Manual sends", "sub": "handled by hand · excluded from sequence", "n": manual, "c": "var(--queue)"},
    ]
    if unsub:
        statuses.append({"k": "Unsubscribed", "sub": "asked to stop · permanently excluded",
                         "n": unsub, "c": "var(--warn)"})

    def kind(r):
        n = note.get(r["email"].lower(), "")
        base = "med spa" if r["audience"] == "medspa" else "vendor"
        if "auto" in n:
            return "auto-reply"
        if "catalog" in n:
            return f"{base} · catalog sent"
        if "needs" in n or not n:
            return f"{base} · needs answer"
        return f"{base} · {n}"

    replies = [{"c": r["domain"], "t": kind(r), "d": day_label(r["sent_at"])[0] if r.get("sent_at") else "-",
                "e": r["email"].split("@", 1)[0] + "@"}
               for r in sorted(replied_rows, key=lambda x: x["last_touch_at"], reverse=True)]
    bounces = [{"a": r["email"], "t": "med spa" if r["audience"] == "medspa" else "vendor"}
               for r in sorted([r for r in rows if r["status"] == "bounced"],
                               key=lambda x: x["last_touch_at"], reverse=True)]

    rate = len(real) / emailed * 100 if emailed else 0
    brate = bounced / emailed * 100 if emailed else 0
    cap, left = ss.DAILY_CAP, max(0, ss.DAILY_CAP - today)
    fu_today = sum(1 for r in rows if r.get("last_touch_at") and r["stage"] != "0"
                   and ss.local_day(r["last_touch_at"]) == ss.local_day(ss.iso(ss.now())))

    kpi = "\n".join([
        f'      <div class="kpi accent"><div class="n mono">{emailed}</div><div class="k">Contacts emailed</div>'
        f'<div class="d">{by_aud.get("vendor",0)} vendors &middot; {by_aud.get("medspa",0)} med spas</div></div>',
        f'      <div class="kpi"><div class="n mono">{today}</div><div class="k">Sent today</div>'
        f'<div class="d">{today - fu_today} first-touch &middot; {fu_today} follow-ups &middot; '
        f'{"daily cap reached" if left == 0 else f"{left} left under the daily cap"}</div></div>',
        f'      <div class="kpi good"><div class="n mono">{len(real)}</div><div class="k">Replies</div>'
        f'<div class="d">{rate:.1f}% of contacts &middot; plus {len(autos)} auto-replies</div></div>',
        f'      <div class="kpi bad"><div class="n mono">{bounced}</div><div class="k">Bounces</div>'
        f'<div class="d">{brate:.1f}% &middot; removed from follow-ups</div></div>',
        f'      <div class="kpi"><div class="n mono">{len(queue_pending):,}</div><div class="k">Still to send</div>'
        f'<div class="d">one contact per business</div></div>',
        f'      <div class="kpi"><div class="n mono">{ss.HOURLY_CAP}</div><div class="k">Per hourly wave</div>'
        f'<div class="d">{cap} per day cap &middot; follow-ups mixed in</div></div>',
    ])

    banner = (f"10 emails per hourly wave, {cap} per day. "
              + (f"The daily cap is spent: {today} of {cap} sent this Pacific day, so the next wave sends "
                 f"after midnight PT." if left == 0
                 else f"{today} of {cap} sent so far today.")
              + " Follow-ups run 3 days apart, 3 steps at most, only to contacts who never replied.")
    when = f"cap reached &middot; next wave 07:00 UTC" if left == 0 else f"{today} of {cap} sent today"

    foot = (f"Follow-up sequence: 3 steps, 3 days apart, only to contacts with no reply or bounce. "
            f"<b>{stages.get('0', 0)}</b> awaiting step 1, <b>{stages.get('1', 0)}</b> at step 1, "
            f"<b>{len(ss.due_followups(rows))}</b> due now.")

    rng = f"{days[0]['d']} – {days[-1]['d']}" if days else "no sends yet"
    rc = (f"{len(real)} warm leads &middot; catalog sent to {len(catalog)} &middot; follow-ups stopped"
          if catalog else f"{len(real)} warm leads &middot; follow-ups stopped")

    def j(o):
        return json.dumps(o, ensure_ascii=False, indent=4).replace("\n", "\n  ")

    page = TPL.read_text(encoding="utf-8")
    for k, v in {
        "{{STAMP}}": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "{{BANNER}}": banner, "{{BANNER_WHEN}}": when, "{{KPI_ROW}}": kpi,
        "{{CHART_RANGE}}": rng, "{{TRACKED}}": str(emailed), "{{STATUS_FOOT}}": foot,
        "{{REPLIES_COUNT}}": rc, "{{DAYS}}": j(days), "{{STATUSES}}": j(statuses),
        "{{REPLIES}}": j(replies), "{{BOUNCES}}": j(bounces),
        "{{SEND_META}}": f"{emailed} sent &middot; {ss.HOURLY_CAP}/hr &middot; {cap}/day",
    }.items():
        assert k in page, f"template is missing {k}"
        page = page.replace(k, v)
    leftover = [x for x in ("{{",) if x in page]
    assert not leftover, "unfilled placeholder remains"
    OUT.write_text(page, encoding="utf-8")
    print(f"dashboard built: emailed={emailed} today={today} replies={len(real)}(+{len(autos)} auto) "
          f"bounced={bounced} pending={len(queue_pending)} days={len(days)}")


if __name__ == "__main__":
    main()
