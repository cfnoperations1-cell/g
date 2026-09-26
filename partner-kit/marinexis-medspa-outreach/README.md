# Marinexis Biologics — med spa outreach

A self-contained outreach agent for [Marinexis Biologics](https://marinexisbiologics.com).
Point Claude at this folder and it will find US med spas that already sell
peptides, email them a wholesale pitch that sends as **you**, follow up on a
fixed cadence, and keep a record of every single send.

It ships with **429 verified med spas** already in the queue and **575 addresses
on a do-not-contact list**, so it will never double up on anyone Jonathan's
campaign has already touched.

---

## What you need

1. **A Claude account** with Claude Code — the desktop app, the web app at
   [claude.ai/code](https://claude.ai/code), or the CLI. Any of them work.
2. **A Gmail or Google Workspace mailbox** you are willing to send business mail
   from. Use a real work address, not a throwaway.
3. **The Gmail connector** enabled in that Claude account. This is what actually
   sends the mail. There is no SMTP password anywhere in this repo and you will
   never be asked for one.

A search API key is optional and only speeds up finding *new* med spas. You do
not need one to start — there is about six weeks of sending already queued up.

---

## Setup

Takes about five minutes, once.

**1. Get the repo onto your machine** (or open it in Claude on the web).

```bash
git clone <this-repo-url>
cd marinexis-medspa-outreach
```

**2. Tell it who you are.**

```bash
cp .env.example .env
```

Open `.env` and fill in two lines:

```
SENDER_NAME=Your Name
SENDER_EMAIL=you@yourdomain.com
```

Those go in the signature of every email. The engine refuses to build a batch
until both are set, on purpose — it will not send mail signed by nobody.

`.env` is gitignored. It never gets committed and never leaves your machine.

**3. Set your starting speed.** Also in `.env`:

```
DAILY_CAP=20
```

Leave it at 20 for the first few days. See **Warming up the mailbox** below.

**4. Connect Gmail in Claude.** In your Claude account, enable the Gmail
connector for the mailbox in `SENDER_EMAIL`. Claude will prompt you to authorize
it the first time it tries to send.

**5. Say hello.** Open the folder with Claude and ask:

> Read CLAUDE.md and tell me the state of the pipeline.

If it reports a queue of 429 and zero sent, you are ready.

---

## Running it

Two commands, both built in as skills. You type them to Claude in plain English
or as a slash command.

### Send a batch — `/outreach-wave`

> Run an outreach wave.

Claude checks for replies and bounces first, builds a batch of 10, sends them
through your Gmail, records every one, refreshes the dashboard, and commits.
Takes a couple of minutes. Run it once an hour during the workday.

To keep it going by itself, ask Claude to set up a recurring task ("run an
outreach wave every hour between 9am and 5pm on weekdays"). It will keep sending
and reporting without you.

### Find more med spas — `/find-medspas`

> Find some more med spas.

Claude searches, crawls each site, and adds only the practices that pass every
gate: a real contact email, peptides actually mentioned on the site, a US
address or phone, and not already in the queue or on the do-not-contact list.

Most candidates get rejected. That is the filter working. Do not ask Claude to
loosen it to hit a number — a bad address costs more than a missing one.

---

## Warming up the mailbox

A mailbox that has never sent bulk email gets throttled fast, and a throttled
domain is slow to recover. Raise the cap as the account earns trust:

| Days sending | `DAILY_CAP` |
|---|---|
| 1–3 | 20 |
| 4–7 | 50 |
| 8 and after | 100 |

Do not go above 100. Jonathan's account got throttled at around 220 in one day;
100 has run clean.

---

## Where everything lives

Everything the agent knows is in plain CSV files you can open in a spreadsheet.

| File | What it holds |
|---|---|
| `outreach/medspa_queue.csv` | Med spas found and verified, not yet emailed |
| `outreach/sent_log.csv` | One row per practice emailed: when, which step, what happened |
| `outreach/do_not_contact.csv` | Never email these. Grows automatically. |
| `exports/lead_pipeline_tracker.html` | The dashboard. Open it in any browser. |
| `emailer/message_medspa.txt` | The opening email. Edit here to change the pitch. |
| `emailer/followup_medspa.txt` | The three follow-ups |

In `sent_log.csv`, the `status` column is the whole picture:

- **active** — emailed, no answer yet, still due for follow-ups
- **replied** — a human wrote back. This is a lead. Follow-ups stop.
- **bounced** — bad address. Follow-ups stop, added to do-not-contact.
- **unsubscribed** — asked to stop. Follow-ups stop, added to do-not-contact.

**Commit and push after every batch.** These files are the agent's entire
memory. A fresh session with an empty log will cheerfully email all 429 people a
second time. The skill does this for you; just do not skip it.

---

## The rules this thing follows

Written out in full in `CLAUDE.md`, which Claude reads before it does anything.
The short version:

- Nobody on the do-not-contact list ever gets an email.
- Every send is recorded in the same breath it is sent.
- The postal address and unsubscribe line stay in every message. That is
  CAN-SPAM, and it is not optional.
- Anyone who asks to stop, stops, permanently.
- Follow-ups go only to people who have not replied and have not bounced. Three
  days apart, three at most, then it lets them be.
- If Gmail returns a quota or block error, it stops sending immediately and
  tells you.

The email only makes claims that are actually true about Marinexis: US labs,
green-list API, a COA with every batch, bulk API and finished vials, one-week
turnaround, cold-chain shipping, wholesale pricing. Claude is told in writing not
to invent certifications, customer names, purity figures, prices or delivery
promises beyond that list. If you ever see it do so, that is a bug — tell
Jonathan.

---

## If something looks wrong

**It says it will not build a wave.** `SENDER_NAME` or `SENDER_EMAIL` is missing
from `.env`. That check is deliberate.

**Gmail returned a quota error.** Stop for the day. Lower `DAILY_CAP` and resume
tomorrow. Do not retry into a block.

**A reply came from a different address than the one emailed.** Common — front
desks forward. The address that was *emailed* is the one that gets marked, or
nothing matches.

**Bounces look high.** Around one in seven is normal on a scraped list. It is a
data-quality fact, not a deliverability failure. It is also a good reason to
leave the daily cap alone.

**You want to change the pitch.** Edit `emailer/message_medspa.txt` and rebuild
the batch. Never edit an email at send time — the copy that goes out has to match
the copy that was recorded.

---

Questions about the product, pricing or a live lead go to Jonathan. Questions
about the agent go to Claude — ask it to read `CLAUDE.md` and explain itself.
