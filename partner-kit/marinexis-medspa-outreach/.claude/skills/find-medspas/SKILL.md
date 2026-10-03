---
name: find-medspas
description: Find new US med spas that already sell peptides and add the qualified ones to the outreach queue. Use when asked to find leads, find med spas, prospect a city or state, or refill the queue.
---

# Find med spas

Three stages: **discover** candidate sites, **enrich** them into contact records,
**promote** the ones that qualify into the send queue. Read `CLAUDE.md` first.

Only practices that already offer peptides belong in the queue. A general day
spa or a dermatology office with no peptide mention is not a prospect, and
adding one wastes a send and risks a complaint.

## 1. Discover

Get the next batch of search queries:

```bash
python3 medspa/discover.py plan 12
```

**If a search API key is set** in `.env` (`SERPER_API_KEY`,
`BRAVE_SEARCH_API_KEY`, or `GOOGLE_CSE_API_KEY` with `GOOGLE_CSE_CX`), run them
automatically:

```bash
python3 medspa/discover.py run 12
```

**If no key is set**, do the searching directly with web search — run each query
from `plan`, then hand back the practice websites found. Skip directory and
listing sites; they are filtered anyway, but they waste a slot:

```bash
python3 medspa/discover.py add "Glow Aesthetics|https://glowaesthetics.com/" "https://vitalitymedspa.com/"
```

A result is worth adding when it looks like one practice's own website. Yelp,
Groupon, Healthgrades, news articles and national telehealth brands are not.

Optionally tag geography, which flows through to the queue:
`add "Austin|TX|Name|https://site.com/"`

## 2. Enrich

```bash
python3 medspa/enrich.py 40
```

This visits each new candidate's home and contact pages and pulls out a contact
email, the peptide terms the site mentions, a US signal, and a phone number.
Run it in batches of 25–50 so a single stall does not cost a long run; progress
is saved every ten sites and re-running skips what is already done.

Expect a meaningful share to come back `no_email` or `unreachable`. Small
practice sites hide behind contact forms and bot protection. That is normal.

## 3. Promote

```bash
python3 medspa/build_queue.py            # dry run, shows what would be added
python3 medspa/build_queue.py --apply    # append to outreach/medspa_queue.csv
```

Always dry-run first and read the skipped counts. A practice reaches the queue
only with a usable email, a peptide mention, a US signal, a non-foreign domain,
and no prior contact by anyone on the team.

**Do not loosen these gates to hit a number.** A thin list is cheaper than a
burned sending domain.

## 4. Commit

```bash
git add -A && git commit -m "Discovery: +N med spas queued" && git push
```

## Reporting

Say how many candidates were found, how many enriched cleanly, how many reached
the queue, and what the queue total is now. If the yield was poor, say which
gate rejected the most and suggest the fix — usually different cities or an
extra term in `medspa/peptide_terms.txt`.
