"""Refresh the outreach numbers in exports/lead_pipeline_tracker.html from sent_log.csv (stdlib only)."""
import csv, re, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_send as ss

AUTO = {"eliteedgebiotech.com", "ironwithin.io"}          # auto-acks, not real replies
rows = ss.load_sent(); t = ss.iso(ss.now())[:10]
vend = sum(1 for r in rows if r["audience"] == "vendor"); ms = sum(1 for r in rows if r["audience"] == "medspa")
init_today = sum(1 for r in rows if r["sent_at"][:10] == t)
fu_today = sum(1 for r in rows if r["last_touch_at"][:10] == t and r["stage"] != "0" and r["sent_at"][:10] != t)
replied = [r for r in rows if r["status"] == "replied"]; real = [r for r in replied if r["domain"] not in AUTO]
bounced = sum(1 for r in rows if r["status"] == "bounced"); manual = sum(1 for r in rows if r["status"] == "manual")
active = [r for r in rows if r["status"] == "active"]; s0 = sum(1 for r in active if r["stage"] == "0"); s1 = len(active) - s0
pend = len(ss.initial_candidates(rows)); dids = ss.load_draft_ids(); drafts_left = sum(1 for e in dids if e not in {r["email"] for r in rows})
budget = max(0, ss.DAILY_CAP - ss.today_count(rows)); n = len(rows)
p = Path(__file__).resolve().parent.parent / "exports" / "lead_pipeline_tracker.html"; s = p.read_text(encoding="utf-8")

def sub(pat, repl, count=1):
    global s
    s, k = re.subn(pat, repl, s, count=count, flags=re.S); assert k, pat[:60]
sub(r'UPDATED <b>[^<]*</b>', f'UPDATED <b>{ss.iso(ss.now())[:16].replace("T", " ")} UTC</b>')
sub(r'<div class="kpi accent"><div class="n mono">[\d,]+</div><div class="k">Contacts emailed</div><div class="d">[^<]*</div>',
    f'<div class="kpi accent"><div class="n mono">{n:,}</div><div class="k">Contacts emailed</div><div class="d">{vend} vendors · {ms} med spas</div>')
sub(r'<div class="kpi"><div class="n mono">[\d,]+</div><div class="k">Sent today</div><div class="d">[^<]*</div>',
    f'<div class="kpi"><div class="n mono">{init_today + fu_today}</div><div class="k">Sent today</div><div class="d">{init_today} first-touch · {fu_today} follow-ups · {budget} left under the daily cap</div>')
sub(r'<div class="kpi good"><div class="n mono">[\d,]+</div><div class="k">Replies</div><div class="d">[^<]*</div>',
    f'<div class="kpi good"><div class="n mono">{len(real)}</div><div class="k">Replies</div><div class="d">{len(real)/n*100:.1f}% of contacts · plus {len(replied)-len(real)} auto-replies</div>')
sub(r'<div class="kpi bad"><div class="n mono">[\d,]+</div><div class="k">Bounces</div><div class="d">[^<]*</div>',
    f'<div class="kpi bad"><div class="n mono">{bounced}</div><div class="k">Bounces</div><div class="d">{bounced/n*100:.1f}% · removed from follow-ups</div>')
sub(r'<div class="kpi"><div class="n mono">[\d,]+</div><div class="k">Still to send</div><div class="d">[^<]*</div>',
    f'<div class="kpi"><div class="n mono">{pend:,}</div><div class="k">Still to send</div><div class="d">one contact per business · {drafts_left} already drafted</div>')
sub(r'<div class="kpi"><div class="n mono">[\d,]+</div><div class="k">(?:Next batch|Per hourly wave)</div><div class="d">[^<]*</div>',
    f'<div class="kpi"><div class="n mono">{ss.HOURLY_CAP}</div><div class="k">Per hourly wave</div><div class="d">{ss.DAILY_CAP} per day cap · existing drafts first</div>')
sub(r'n:\d+, c:"var\(--accent\)"', f'n:{len(active)}, c:"var(--accent)"')
sub(r'sub:"[^"]*", n:\d+, c:"var\(--done\)"', f'sub:"{len(real)} real replies + {len(replied)-len(real)} auto-replies · no more automated mail", n:{len(replied)}, c:"var(--done)"')
sub(r'sub:"address rejected · excluded", n:\d+', f'sub:"address rejected · excluded", n:{bounced}')
sub(r'sub:"catalog sent by hand · excluded from sequence", n:\d+', f'sub:"catalog sent by hand · excluded from sequence", n:{manual}')
sub(r'<b>\d+</b> awaiting step 1, <b>\d+</b> at step 1', f'<b>{s0}</b> awaiting step 1, <b>{s1}</b> at step 1')
sub(r'<h3>Contact status <span class="count">[^<]*</span>', f'<h3>Contact status <span class="count">{n} tracked</span>')
sub(r'\{name:"Send",\s+st:"[a-z]+",\s+meta:"[^"]*"\}', f'{{name:"Send",       st:"run",   meta:"{n} sent · 10/hr · 100/day"}}')
p.write_text(s, encoding="utf-8"); print(f"dashboard refreshed: emailed={n} today={init_today}+{fu_today} replies={len(real)} bounces={bounced} pending={pend}")
