---
name: outreach-wave
description: Send one hourly wave of the Marinexis med spa campaign and record it. Use when asked to send a wave, run outreach, or resume the campaign.
---

# Hourly outreach wave

One wave is six steps: check the inbox, check the budget, build, send, record,
schedule. Read `CLAUDE.md` first — the hard rules there govern this skill.

The tracking CSVs are the only memory this campaign has. Recording happens in
the same turn as sending, or a practice gets emailed twice.

## 1. Check the inbox

Look for replies and for bounce notices from the last two days, and compare them
against `outreach/sent_log.csv`.

Mark whatever turned up:

```bash
echo "someone@practice.com" > /tmp/replied.txt
python3 outreach/serve_send.py mark replied /tmp/replied.txt
```

Use `mark bounced` for undeliverable addresses and `mark unsubscribed` for
anyone who asks to stop. Both are added to the do-not-contact list permanently.

Two things that trip people up:

- A bounce notice often does not name the failed address in its metadata. Open
  the message and read it to find the `Final-Recipient` line.
- A reply can arrive from a different address than the one that was emailed.
  Mark the address that **was emailed**, or nothing matches.

Autoresponders count as replied. Newsletters and spam are ignored.

## 2. Check the budget

```bash
python3 outreach/serve_send.py stats
```

If `budget_left_today=0` or the batch would be empty, do not send. Skip to step
6. If `campaign=complete`, stop and say so: the queue is empty and discovery
needs to run.

## 3. Build the wave

```bash
python3 outreach/serve_send.py next 10 /tmp/wave.json
```

Print the file and read it before sending. Check that no `{placeholder}` is left
in a subject or body, and that each business name reads like a business rather
than scraped page furniture like "Book Now" or "Home". If junk gets through, add
the pattern to `JUNK_NAME` in `outreach/serve_send.py` and rebuild.

## 4. Send

Send each item with the Gmail connector, copying `to`, `subject` and `body`
verbatim from the wave file. Never edit copy at send time.

If Gmail returns a quota, rate-limit or block error, stop sending immediately,
record only what actually went out, and tell the user the account was throttled.

## 5. Record and publish

```bash
python3 outreach/serve_send.py record /tmp/wave.json 10
python3 outreach/serve_send.py export
python3 outreach/dash_update.py
git add -A && git commit -m "Outreach wave: +10 sent" && git push
```

`10` is how many actually sent. If some failed, name them:
`record /tmp/wave.json 10 --skip 3,7`

## 6. Schedule the next one

Schedule the next wave about an hour out so the loop continues without being
asked.

Then report briefly, and only when something happened: a new reply, a block, the
daily cap reached, the queue running dry. A wave that idled on a spent budget is
one line.
