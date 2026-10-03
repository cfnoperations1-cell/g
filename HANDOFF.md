# Marinexis outreach: handoff

State as of **Oct 3, 2026, 03:00 UTC**. Use this to pick the campaign up in another Claude account.

## Starting in the new account

1. Unzip this folder, or clone `cfnoperations1-cell/g`, branch `claude/new-session-ao9mgd`, if the new account has GitHub access.
2. Connect the **Gmail** connector for jonathan@marinexisbiologics.com. Every send, bounce check and reply check goes through it.
3. Paste this to Claude:

   > Read HANDOFF.md and continue the Marinexis outreach exactly as described there. Follow every rule in it. Start with the inbox check, then the next wave.

4. **Stop the old account first.** The old session has a send scheduled for Oct 3 07:05 UTC. If both accounts send, prospects get the same email twice. Tell the old session "stop the waves", or delete the "Wave 1 (Oct 3)" routine under Routines on claude.ai.

## What this is

Jonathan Cole, Marinexis Biologics, 6671 S Las Vegas Blvd, Las Vegas, NV 89118, emails from jonathan@marinexisbiologics.com. It is cold outreach selling US-made peptides and GLP-1s at wholesale.

Two audiences are live:

- **RUO vendors** (`vendor`): research-peptide brands, pitched as a domestic source. This list is finished; every eligible vendor has been emailed.
- **Practices** (`practice`): Jonathan's purchased list, `scraper/lists/Medspa_US_2.csv`. It covers med spas, wellness, weight-loss, plastic surgery and similar. Each email goes to the named owner, best-ranked first.
  - The ranked queue is `exports/practice_queue_ranked_2026-09-30.pdf`.
  - 76 have been sent and about 4,330 remain.

Held, not sending:

- **Telehealth** (96 personal emails plus a 429-row hunt). Jonathan said telehealth buyers need a **503A** pharmacy. Nothing goes out until he decides how to handle that.
- **The old med spa backlog.** Jonathan said "MedSpa is truly don't work" for the earlier generic list. His own list is the exception.

## Rules (Jonathan's, all still in force)

- **50 emails a day, in waves of 10, about one an hour.** The first wave is at 07:05 UTC (12:05 AM Pacific).
- **Never invent or guess an email address.**
- **Never email anyone in** `outreach/do_not_contact.csv` or `outreach/sent_log.csv`.
- **Never quote a brand-name drug** as something we supply.
- **Never loosen the US or peptide gates** to hit a number.
- **Never add a business whose site could not be read.** Exception: Jonathan's practice list. He said "Don't read the sites do it based on the type of doctor."
- **A bounce is never retried.**
  - A Microsoft `550 5.4.1 Recipient address rejected` is a bounce.
  - "Delivery incomplete" / "Delay" is **not** a bounce. Gmail is still retrying.
- **Stop immediately and tell Jonathan** on any Gmail quota, limit or block error against us. The domain has been throttled once already.
- **Practice bounce watch:** if more than 5 of any 50 practice emails bounce, stop and tell Jonathan.
- **Never send follow-up emails without Jonathan's approval.**
- **Never answer prospects' questions.** Log them and flag them to Jonathan. Check his Sent folder first, since he may already have answered.
- **Copy goes out verbatim** from `serve_send.py next`, CAN-SPAM footer included.
- **Hospital systems, deans and other non-buyers are held.**
  - Add them to `DECLINED_CONTACTS` in `outreach/serve_send.py`.
  - Also add them to `outreach/exclusions_expected.txt`. The sender refuses to build a wave if the two lists disagree.
- Don't hand-edit `exports/dashboard_artifact.html` or `exports/crm_artifact.html`. Rebuild them with the scripts below.

## Wave procedure (each hour)

```bash
S=<a scratch folder>

# 1. Inbox check in Gmail. Use newer_than:20h on the first wave of the day,
#    newer_than:2h after that, with -in:sent. Read every new message.
#    Bounces go in a text file, one address per line; same for replies:
python3 outreach/serve_send.py mark bounced $S/bounces.txt
python3 outreach/serve_send.py mark replied $S/replies.txt
#    For each reply, append a row to outreach/reply_notes.csv: email,"NOTE".
#    Start the note with a label the CRM reads: INTERESTED / READY TO BUY /
#    CATALOG SENT / DECLINED / NEEDS JONATHAN / auto-reply.

# 2. Build the next 10. Always pass an output path.
FU_SHARE=0 python3 outreach/serve_send.py next 10 $S/wave.json

# 3. Verify every practice row against the template before sending:
python3 - <<'EOF'
import json, csv, sys
sys.path.insert(0, 'scraper'); sys.path.insert(0, 'outreach')
from import_medspa_list import render
d = json.load(open('WAVE_JSON_PATH'))
C = d if isinstance(d, list) else d.get('batch') or d.get('rows') or d.get('items')
pl = {r['email']: r for r in csv.DictReader(open('scraper/hunts/practice_list_2026-09-29.csv'))}
for r in C:
    p = pl[r['to']]
    s, b = render(p['vendor'], p['first_name'], p['practice'])
    print(r['to'], s == r['subject'] and b == r['body'], p['category'], p['position'])
EOF

# 4. Send each row with Gmail send_message, using `to`, `subject` and `body`
#    exactly as they appear in wave.json.

# 5. Record and rebuild:
python3 outreach/serve_send.py record $S/wave.json
python3 outreach/dash_build.py      # -> exports/dashboard_artifact.html
python3 outreach/crm_build.py       # -> exports/crm_artifact.html

# 6. Commit and push, then publish the dashboard and CRM pages again.
```

The old account's dashboard and CRM links are private to that account. In the new account, publish `exports/dashboard_artifact.html` and `exports/crm_artifact.html` as new artifacts. The CRM page uses the artifact `db` capability, collection `accounts`. It has no hand-entered notes yet, so nothing is lost by starting fresh.

## Open items for Jonathan (most important first)

1. **PepEssentials, Jake** (info@pepessentials.com, AZ). Buys 500–1,000 kits per product and sent a screenshot of an order he planned to place Oct 1–2. Close it.
2. **Forward Peptides** (support@forwardpeptidesco.com). Oct 1 20:47: "What number would you like me to call?" Unanswered. JC's number is 973-281-7269.
3. **GLP1 Research Lab** (info@glp1researchlab.com). "Feel free to send pricing." Owed the catalog.
4. **Stoneheart, Corey** (stoneheart.group.llc@gmail.com). Price-led. His current prices:
   - GHK-Cu 100mg: $21 per 10 vials
   - Tirzepatide 60mg: $128 per 10
   - Retatrutide 50mg: $162 per 10
5. **Luxe Level Aesthetics.** The owner sold the business. The new owner is at Info@luxelevelaesthetics.com, which has not been emailed; JC's call.
6. **Clear Span Solutions, Maria Rodriguez** (maria@clearspansolutions.com, 910-703-7894). Distributor partner. She asked about:
   - discount tiers
   - the pharmacy hand-off
   - whether shipping is priced separately
7. **Novalab, Lina Muñoz** (linamunoz.1988@gmail.com). Asked whether we ship to Spain.
8. **Calls:**
   - Klene, Sunji: 541-499-1640
   - Oath Research, Greg: 480-999-1097
   - Amino Wholesale: 323-592-7138
9. **Catalogs owed:** PX Peptides and ProtidexBio.
10. **AminoForge LLC** paid by Wells Fargo; the amount still needs entering in the CRM.
11. **Decision:** the 503A question for telehealth.
    - The recommendation is to email the 27 compounding pharmacies in `scraper/hunts/compounding_2026-09-29.csv` with a pharmacy-specific email Jonathan approves.
    - 59 chains, supplement brands and adjacent leads also need their own copy.

Full reply history: `outreach/reply_notes.csv`.

## Numbers (end of Oct 2)

- **Emailed:** 1,429 businesses
- **Replies:** 79 real plus 24 auto-replies
- **Bounced:** 102
- **Ordered:** 1 (AminoForge)
- **Negotiating:** 4
- **Interested:** 19
- **Catalog sent:** 44

## File map

| Path | What it is |
|---|---|
| `outreach/serve_send.py` | The sender. It builds waves, records sends, marks bounces and replies, and holds the block lists. |
| `outreach/sent_log.csv` | Everyone ever emailed, with status. The source of truth. |
| `outreach/draft_queue.csv` | Every drafted email not yet sent, ranked. |
| `outreach/reply_notes.csv` | What each replier said. |
| `outreach/catalog_sent.csv` | Who received the catalog. |
| `outreach/do_not_contact.csv` | Never email these. |
| `outreach/exclusions_expected.txt` | Second copy of the block list. The sender checks the two against each other. |
| `outreach/personal_leads_2026-09-29.csv` | The 101 personal emails Jonathan approved. |
| `outreach/dash_build.py`, `outreach/crm_build.py` | Build the dashboard and CRM pages. |
| `emailer/message_practice.txt` | The practice email template. |
| `emailer/message_telehealth.txt` | Telehealth draft; held. |
| `scraper/import_medspa_list.py` | Imports Jonathan's practice list. |
| `scraper/rank_queue.py` | Ranks the queue best-first. |
| `scraper/lists/Medspa_US_2.csv` | Jonathan's purchased list. |
| `scraper/hunts/` | Lead hunts: RUO vendors, telehealth, compounding pharmacies, chains. Each has a `_rejected` file. |
| `exports/` | Dashboard, CRM, the ranked-queue PDF, and to-do lists. |
