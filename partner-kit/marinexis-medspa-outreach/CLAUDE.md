# Marinexis Biologics — med spa outreach agent

This repo is a working agent, not a library. Claude runs it: finding med spas
that already sell peptides, emailing them a US-made supply pitch, tracking every
send, and following up on a fixed cadence.

Read this file before doing anything in this repo.

---

## The business

**Marinexis Biologics** manufactures peptides in the United States and supplies
them wholesale to practices and brands.

| | |
|---|---|
| Company | Marinexis Biologics |
| Address | 6671 S Las Vegas Blvd, Las Vegas, NV 89118 |
| Website | marinexisbiologics.com |
| Sells | Bulk API and finished vials, wholesale |
| Buyers | Med spas, wellness and longevity clinics, peptide brands, labs |

### What we can say in an email

These are the claims the pitch rests on. They are true, and they are the whole
list. Do not invent specifics beyond them — no fabricated certifications,
customer names, purity percentages, prices or turnaround promises.

- Four labs in the United States, running on green-list API
- A third-party certificate of analysis (COA) ships with every batch
- Both bulk API and finished vials
- One-week turnaround, even on large runs
- Cold-chain shipping direct to the practice
- Wholesale per-vial pricing that generally beats a middleman
- No customs risk, and consistent purity batch to batch
- A "Made in USA" wholesale catalog with per-vial and per-mg pricing, sent on request

### Who we email here

US med spas, aesthetics practices and wellness clinics that **already offer
peptides** — semaglutide, tirzepatide, BPC-157, NAD+, sermorelin and the rest of
`medspa/peptide_terms.txt`. A practice that does not mention peptides anywhere on
its site is not a prospect and should never enter the queue.

Vendors and peptide brands are a separate campaign run out of Jonathan's repo.
Everyone he has contacted is already in `outreach/do_not_contact.csv`; never
email them from here.

---

## Hard rules

These are not style preferences. Breaking one costs the sending domain.

1. **Never email anyone in `outreach/do_not_contact.csv`.** The send engine
   enforces this, so the rule in practice is: never bypass the engine.
2. **Every send gets recorded** with `serve_send.py record`, in the same turn it
   was sent. An unrecorded send is how somebody gets emailed twice.
3. **Copy goes out verbatim** from what `serve_send.py next` produced. Do not
   rewrite, personalize further, or "improve" a body at send time. To change the
   pitch, edit `emailer/message_medspa.txt` and rebuild the wave.
4. **Respect the caps.** 10 per hour, 100 per day, and on a brand-new mailbox
   start lower (see Warm-up). If Gmail returns a quota, limit or block error:
   stop sending immediately, record only what actually went out, and say so.
5. **CAN-SPAM footer stays in every message** — the postal address and the
   unsubscribe line. They are in the templates; leave them there.
6. **Anyone who asks to stop, stops.** Run
   `serve_send.py mark unsubscribed addrs.txt`, which also adds them to the
   do-not-contact list permanently.
7. **Follow-ups only to people who have not replied or bounced**, 3 days apart,
   3 at most. The engine handles this; do not hand-send follow-ups.
8. **Commit and push after every wave.** The CSVs are the only memory this agent
   has. State that is not committed is state that gets lost and double-sent.

---

## Setup (once)

```bash
cp .env.example .env
```

Fill in `SENDER_NAME` and `SENDER_EMAIL` — the emails send as **you**, not as
Jonathan. The engine refuses to build a wave until both are set. Company name
and postal address are fixed in code and do not need to be set.

Connect the **Gmail connector** in Claude for the mailbox in `SENDER_EMAIL`.
That is what actually sends; there is no SMTP password anywhere in this repo.

Search API keys are optional. With one, discovery runs itself. Without one,
Claude does the searching with its own web search tool — see below.

### Warm-up

A mailbox that has never sent bulk gets throttled fast. Set `DAILY_CAP` in
`.env` and raise it as the account earns trust:

| Days sending | `DAILY_CAP` |
|---|---|
| 1–3 | 20 |
| 4–7 | 50 |
| 8+ | 100 |

Jonathan's account got throttled at roughly 220 in a day. 100 is the ceiling
that has proven stable.

---

## The two jobs

### 1. Find med spas — `/find-medspas`

Three steps, and the first one has two paths depending on whether a search API
key is configured.

```bash
python3 medspa/discover.py plan 12        # next 12 search queries
python3 medspa/discover.py run 12         # with an API key: search them now
# without a key: Claude runs each query with web search, then
python3 medspa/discover.py add "Name|https://site.com/" ...

python3 medspa/enrich.py 40               # crawl each site: email, peptides, US signal
python3 medspa/build_queue.py             # dry run: what would be added
python3 medspa/build_queue.py --apply     # append the qualified ones to the queue
```

`build_queue.py` only promotes a practice that has a real contact email, mentions
peptides, shows a US signal, is not foreign, and is not a duplicate. Expect a
large share of candidates to fall out at that gate. That is the filter working;
do not loosen it to hit a number.

### 2. Send a wave — `/outreach-wave`

Runs hourly. Scan for replies and bounces, build 10, send, record, commit.
The full runbook is in `.claude/skills/outreach-wave/SKILL.md`.

```bash
python3 outreach/serve_send.py stats
python3 outreach/serve_send.py next 10 wave.json
# send each item with the Gmail connector: to, subject, body copied verbatim
python3 outreach/serve_send.py record wave.json 10
python3 outreach/serve_send.py export
python3 outreach/dash_update.py
git add -A && git commit -m "Outreach wave: +10 sent" && git push
```

---

## File map

```
outreach/
  serve_send.py        the send engine: caps, cadence, dedup, rendering
  medspa_queue.csv     verified med spas not yet contacted  (429 seeded)
  sent_log.csv         one row per med spa emailed: when, stage, status
  do_not_contact.csv   never email these  (575 seeded from Jonathan's campaign)
  contacted.csv        \
  replied.csv           |  written by `serve_send.py export`
  bounced.csv           |
  not_yet_emailed.csv  /
  dash_update.py       refresh the HTML dashboard from sent_log.csv

medspa/
  discover.py          search -> candidates.csv
  enrich.py            crawl candidates -> enriched.csv
  build_queue.py       enriched.csv -> medspa_queue.csv (with quality gates)
  cities.txt           metros to work through
  peptide_terms.txt    what qualifies a practice, and what the email quotes

emailer/
  message_medspa.txt   the opening email
  followup_medspa.txt  follow-ups 1-3 (the stage line changes per step)

exports/
  lead_pipeline_tracker.html   dashboard, refreshed each wave
```

### Status meanings in `sent_log.csv`

| status | means |
|---|---|
| `active` | emailed, no response yet — still eligible for follow-ups |
| `replied` | a human wrote back — follow-ups stop, this is a lead |
| `bounced` | undeliverable — follow-ups stop, added to do-not-contact |
| `unsubscribed` | asked to stop — follow-ups stop, added to do-not-contact |

`stage` is how many follow-ups have gone out: `0` means only the opener.

---

## Notes for whoever runs this

- The queue ships with **429 med spas** already found, verified and deduped
  against Jonathan's campaign. There is roughly six weeks of sending in it at
  100/day before discovery even matters.
- Bounces run high on scraped lists — Jonathan saw about 14 per 100 on one day.
  That is a data-quality fact, not a deliverability problem, but it is a reason
  to keep the daily cap where it is.
- Replies that arrive within seconds of a send are almost always autoresponders.
  Mark them `replied` anyway; they should not get follow-ups either way.
- When in doubt about whether to send something, don't. Ask.
