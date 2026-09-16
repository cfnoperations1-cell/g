"""Rebuild exports/lead_pipeline_tracker.html from the tracking CSVs.

Standard library only. Reads sent_log.csv and medspa_queue.csv, writes a single
self-contained HTML file with no external assets, so it opens anywhere and can
be shared as-is.

    python3 outreach/dash_update.py
"""
import csv, html, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "outreach"))
import serve_send as ss

OUT = ROOT / "exports" / "lead_pipeline_tracker.html"


def read(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    rows = ss.load_sent()
    queue = read(ROOT / "outreach" / "medspa_queue.csv")
    st = Counter(r["status"] for r in rows)
    emailed = len(rows)
    replied = st.get("replied", 0)
    bounced = st.get("bounced", 0)
    unsub = st.get("unsubscribed", 0)
    pending = len(ss.initial_candidates(rows))
    today = ss.today_count(rows)
    rate = (replied / emailed * 100) if emailed else 0.0

    by_day = Counter(ss.local_day(r["last_touch_at"]) for r in rows if r["last_touch_at"])
    days = sorted(by_day)[-14:]
    peak = max((by_day[d] for d in days), default=1) or 1

    by_state = Counter((r.get("state") or "").strip() for r in queue if (r.get("state") or "").strip())
    replied_rows = [r for r in rows if r["status"] == "replied"]

    def bars():
        out = []
        for d in days:
            n = by_day[d]
            pct = round(n / peak * 100)
            out.append(
                f'<div class="bar"><div class="bar-fill" style="height:{pct}%" title="{n} sent"></div>'
                f'<div class="bar-n">{n}</div><div class="bar-d">{html.escape(d[5:])}</div></div>')
        return "".join(out) or '<p class="muted">No sends yet.</p>'

    def state_rows():
        out = []
        for s, n in by_state.most_common(10):
            pct = round(n / max(by_state.values()) * 100)
            out.append(f'<tr><td>{html.escape(s)}</td><td class="num">{n}</td>'
                       f'<td><div class="mini" style="width:{pct}%"></div></td></tr>')
        return "".join(out) or '<tr><td colspan="3" class="muted">No geography on file.</td></tr>'

    def reply_rows():
        out = []
        for r in sorted(replied_rows, key=lambda x: x["last_touch_at"], reverse=True)[:25]:
            out.append(f'<tr><td>{html.escape(r["domain"])}</td><td>{html.escape(r["email"])}</td>'
                       f'<td class="muted">{html.escape(r["last_touch_at"][:10])}</td></tr>')
        return "".join(out) or '<tr><td colspan="3" class="muted">No replies yet.</td></tr>'

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Med Spa Pipeline</title>
<style>
  :root {{
    --bg:#f6f7f9; --card:#fff; --ink:#15181d; --muted:#6b7280; --line:#e5e7eb;
    --accent:#2f6f4e; --accent-soft:#e6f0ea; --warn:#b45309;
  }}
  :root:not([data-theme="light"]) {{ }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#0f1115; --card:#171a20; --ink:#e8eaed; --muted:#9aa2ae; --line:#272b33;
      --accent:#5fbf8e; --accent-soft:#1b2c24; --warn:#d9a441;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg:#0f1115; --card:#171a20; --ink:#e8eaed; --muted:#9aa2ae; --line:#272b33;
    --accent:#5fbf8e; --accent-soft:#1b2c24; --warn:#d9a441;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system,BlinkMacSystemFont,
        "Segoe UI",Roboto,Helvetica,Arial,sans-serif; padding:32px 16px 64px; }}
  .wrap {{ max-width:1000px; margin:0 auto; }}
  h1 {{ font-size:24px; margin:0 0 4px; letter-spacing:-.01em; }}
  h2 {{ font-size:15px; margin:0 0 14px; font-weight:600; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:24px; }}
  .grid {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); margin-bottom:24px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .kpi .v {{ font-size:28px; font-weight:650; letter-spacing:-.02em; }}
  .kpi .l {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; margin-top:2px; }}
  .kpi .h {{ color:var(--muted); font-size:12px; margin-top:6px; }}
  .accent .v {{ color:var(--accent); }}
  .panel {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:20px; margin-bottom:16px; }}
  .chart {{ display:flex; align-items:flex-end; gap:8px; height:160px; }}
  .bar {{ flex:1; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; height:100%; }}
  .bar-fill {{ width:100%; background:var(--accent); border-radius:4px 4px 0 0; min-height:3px; }}
  .bar-n {{ font-size:11px; color:var(--muted); margin-top:4px; }}
  .bar-d {{ font-size:10px; color:var(--muted); }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.05em;
       color:var(--muted); font-weight:600; padding:0 8px 8px 0; border-bottom:1px solid var(--line); }}
  td {{ padding:8px 8px 8px 0; border-bottom:1px solid var(--line); }}
  td.num {{ font-variant-numeric:tabular-nums; }}
  .mini {{ height:8px; background:var(--accent-soft); border:1px solid var(--accent); border-radius:4px; }}
  .muted {{ color:var(--muted); }}
  .two {{ display:grid; gap:16px; grid-template-columns:1fr 1fr; }}
  @media (max-width:720px) {{ .two {{ grid-template-columns:1fr; }} }}
</style></head><body><div class="wrap">

<h1>Med spa pipeline</h1>
<div class="sub">Marinexis Biologics &middot; refreshed {stamp}</div>

<div class="grid">
  <div class="card kpi"><div class="v">{emailed}</div><div class="l">Emailed</div>
    <div class="h">{today} today</div></div>
  <div class="card kpi accent"><div class="v">{replied}</div><div class="l">Replied</div>
    <div class="h">{rate:.1f}% of sends</div></div>
  <div class="card kpi"><div class="v">{bounced}</div><div class="l">Bounced</div>
    <div class="h">{unsub} unsubscribed</div></div>
  <div class="card kpi"><div class="v">{pending}</div><div class="l">Queue left</div>
    <div class="h">verified, not yet emailed</div></div>
</div>

<div class="panel"><h2>Sends per day (last 14 active days)</h2>
  <div class="chart">{bars()}</div></div>

<div class="two">
  <div class="panel"><h2>Queue by state</h2>
    <table><thead><tr><th>State</th><th>Leads</th><th></th></tr></thead>
    <tbody>{state_rows()}</tbody></table></div>
  <div class="panel"><h2>Replies</h2>
    <table><thead><tr><th>Practice</th><th>Address</th><th>Last touch</th></tr></thead>
    <tbody>{reply_rows()}</tbody></table></div>
</div>

</div></body></html>"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc, encoding="utf-8")
    print(f"dashboard refreshed: emailed={emailed} today={today} replies={replied} "
          f"bounces={bounced} queue={pending}")


if __name__ == "__main__":
    main()
