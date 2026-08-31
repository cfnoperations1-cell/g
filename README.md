# Peptide Industry Lead-Gen: Scrapers + CRM + Outreach

A system for finding two different kinds of US peptide lead and running
outreach to both:

1. **Vendor scraper agent** (`scraper/`) — companies that *sell* peptides.
   Discovers candidates via search and business-directory APIs, visits each
   company's own public website, classifies it into a target company type,
   guesses whether it's US-based, and saves qualifying companies as leads.
2. **Clinic scraper agent** (`clinics/`) — US medical clinics that *offer*
   peptides to patients: med spas, hormone/TRT clinics, medical weight-loss
   clinics, regenerative and functional-medicine practices, and their
   telehealth equivalents. Geo-targeted and built around a **daily quota**
   (400 new leads a day by default).
3. **CRM** (`crm/`) — a small local Flask + SQLite web app to view leads,
   filter by kind, status and type, and track outreach notes.
4. **Emailer agent** (`emailer/`) — writes outreach to leads that have an
   email address, records every message so nobody is contacted twice, and
   defaults to writing drafts for review rather than sending. Vendors and
   clinics get different copy, chosen automatically from the lead's kind.

All of it shares one SQLite database (`data/leads.db`) via `db.py` /
`models.py`. Every lead carries a `kind` of `vendor` or `clinic`, which is
what keeps the two campaigns from crossing. Two schedulable pipelines run a
discovery agent and the emailer back to back: `pipeline.py` for vendors and
`clinic_pipeline.py` for clinics.

## Vendor company types

Every *vendor* lead is classified into one of four categories, based on what
its own site says (clinic types are covered further down, under the clinics
agent):

- `research_only` — sells peptides labeled "for research use only" / "not
  for human consumption"
- `consumer_and_research` — sells peptides with no research-only
  restriction (i.e. to consumers as well as researchers)
- `compounding_pharmacy` — a licensed compounding pharmacy (detected via
  phrases like "compounding pharmacy", "PCAB accredited", "USP 795/797")
- `manufacturing_lab` — a peptide manufacturing/synthesis lab (detected via
  phrases like "cGMP", "custom peptide synthesis", "API manufacturer")

A site only gets classified at all if it actually mentions one of your
tracked peptide keywords (`scraper/peptide_keywords.txt`) — otherwise it's
skipped as irrelevant. By default, only leads that also look US-based are
kept (see below); pass `--allow-non-us` to disable that filter.

By default the agent only actively *searches* for `research_only` and
`consumer_and_research` companies (B2C peptide brands and general peptide
sellers) — compounding pharmacies and manufacturing labs are still
correctly classified and saved if one happens to turn up, just not a
search target by default. Pass `--company-types` to change this, e.g.
`--company-types research_only,consumer_and_research,compounding_pharmacy`.

### US-presence detection

There's no reliable universal "give me this company's country" signal from
a public website, so this is a best-effort heuristic: a site counts as
US-based if it mentions "USA"/"United States", or if a US state name (or a
state abbreviation sitting next to a zip code, to avoid false positives on
stray two-letter words) appears in its text. The detected state, when
found, is saved on the lead. Search queries also request US-biased results
(Google CSE `cr=countryUS`, Bing `mkt=en-US`, Places `region=us`) as a first
pass, with the site-text heuristic as a second check.

## Why APIs instead of raw scraping?

Scraping Google/Bing/LinkedIn search-result pages directly violates their
terms of service and gets IP addresses blocked quickly. Instead, this agent
uses their official search/directory APIs to *discover* candidate company
URLs, then visits each company's *own* public site (home/about/contact pages
only) to read information the company itself has published — while
respecting `robots.txt`, identifying itself with a descriptive User-Agent,
and pausing between requests.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes requirements.txt + pytest

cp .env.example .env
# edit .env and add whichever API keys you have (see below)
```

### Getting API keys (only need one search provider to start)

- **Serper.dev** (easiest -- start here): one API key, no Google Cloud
  project, no billing setup. Sign up at https://serper.dev/, copy the key
  into `SERPER_API_KEY`. Returns Google results.
- **Brave Search API**: one API key, independent index, no Google Cloud.
  https://brave.com/search/api/ -> `BRAVE_SEARCH_API_KEY`.
- **Google Custom Search** (most setup: needs a Cloud project, the API
  enabled, and a linked billing account; free tier = 100 queries/day):
  1. Go to https://programmablesearchengine.google.com/ and create a new
     search engine. Under "Sites to search", choose "Search the entire web".
  2. Copy its **Search engine ID** — that's `GOOGLE_CSE_CX`.
  3. Go to https://console.cloud.google.com/apis/credentials, create a
     project if needed, click "Create credentials" -> "API key", and enable
     the **Custom Search API** for that project. That key is
     `GOOGLE_CSE_API_KEY`.
  4. Paste both into `.env`.
- **Bing Web Search API** (optional second search provider): create an
  Azure Cognitive Services "Bing Search v7" resource and use its key as
  `BING_SEARCH_API_KEY`.
- **Google Places API** (optional, directory-based discovery): enable
  "Places API" in Google Cloud Console and set `GOOGLE_PLACES_API_KEY`.

Any provider left blank is simply skipped at runtime (you'll see a log line
saying so) — the agent still runs with whatever you've configured.

### Peptide directory sites (thepeptidelist.com, peptidebase.io)

Checked as discovery sources; **neither permits programmatic access to its
listings**, so the agent does not crawl them:

- `peptidebase.io` sits behind a Cloudflare challenge that returns 403 to
  every non-browser request, including `/robots.txt`.
- `thepeptidelist.com` serves its homepage and article pages but returns
  `403 Your request was blocked` for `/providers` and every provider
  profile. Its vendor data loads from `/api/`, which its own `robots.txt`
  disallows, and its `llms.txt` asks that content not be used for "bulk
  derivative datasets". The machine-readable mirror it does publish
  (`/providers.md`) deliberately contains only category descriptions, not
  the listings.

Both are still useful to you *manually*: browse them yourself, and paste
any company URLs worth pursuing into `scraper/seed_urls.txt` — the pipeline
processes them from there.

### A note if you're running this inside a sandboxed cloud dev environment

Some hosted dev environments (e.g. Claude Code on the web with a restricted
network policy) only allow outbound connections to a pre-approved list of
domains (package registries, well-known APIs, etc.) and will reject
connections to arbitrary company websites with a proxy-level 403 — which can
look like every site's robots.txt is disallowing everything. If that
happens, either switch that environment's network policy to full internet
access, or run the agent on a machine/environment without that restriction.

## Running the vendor scraper agent

```bash
# Dry run first: prints what it would find, writes nothing to the DB
python -m scraper.agent --dry-run -v --max-queries 10

# Real run: populates data/leads.db
python -m scraper.agent --max-queries 20 --per-query 10 --limit 50
```

Flags:
- `--keywords-file PATH` — override the default peptide list
  (`scraper/peptide_keywords.txt`)
- `--max-queries N` — cap total queries sent to search/directory APIs
  (default 20 — mind free-tier daily quotas; queries are interleaved so a
  small budget still samples every company type)
- `--per-query N` — results to request per query, per provider (default 10)
- `--limit N` — stop after visiting N new candidate sites
- `--allow-non-us` — also keep leads that don't look US-based (by default
  only US companies are kept)
- `--company-types` — comma-separated list of company types to actively
  search for (default: `research_only,consumer_and_research`)
- `--seed-urls-file PATH` — skip search/directory discovery entirely and
  visit exactly the URLs listed in this file (one per line, `#` for
  comments). Useful for testing the fetch -> classify -> save pipeline
  without burning search-API quota, or for feeding in a known company list.
- `--dry-run` — print results without writing to the database
- `-v` — verbose logging

Edit `scraper/peptide_keywords.txt` to change which compounds it searches
for, and `scraper/query_templates.py` to change the query phrasing per
company type.

## Running the CRM

```bash
python run_crm.py
# open http://127.0.0.1:5000
```

View all leads, filter by kind (vendor / clinic), by status (new / contacted
/ replied / qualified / disqualified) and by type, open a lead to see
extracted contact info, detected location, and the classification evidence,
and add notes. Clinic leads also show their clinic type, city, whether they
run telehealth, and which tracked compounds their site names.

## Running tests

```bash
pytest
```

Tests cover the pure logic (email extraction, US-presence/company-type
classification, dedup/upsert rules, query building) with no network calls
or external services.

## Project layout

```
config.py, db.py, models.py     # shared config + SQLAlchemy engine + Lead model
scraper/
  search_providers.py           # Google CSE / Bing search API wrappers
  directory_providers.py        # Google Places directory API wrapper
  site_parser.py                # fetches a company's site, extracts contact info
  classify.py                   # company-type classification + US-presence heuristic
  query_templates.py            # per-company-type search query templates
  peptide_keywords.txt          # editable list of tracked peptides/compounds
  agent.py                      # orchestrates: search -> visit -> classify -> save
clinics/
  agent.py                      # daily-quota clinic discovery (search -> visit -> classify -> save)
  classify.py                   # clinic detection, clinic-type + telehealth heuristics
  query_templates.py            # geo-targeted search + Places query templates
  us_cities.txt                 # ~450 US cities crossed into the query space
  clinic_services.txt           # editable list of clinic-side services/treatments
crm/
  routes.py, templates/, static/  # Flask views for browsing/updating leads
run_crm.py                      # CRM entrypoint
pipeline.py                     # daily vendor loop
clinic_pipeline.py              # daily clinic loop (400 new leads/day)
tests/                          # unit tests (no network)
```

## Outreach agent

Contacts leads the scraper found. Reads the message from
`emailer/message.txt` (edit it freely — first line is the `Subject:`).

```bash
python -m emailer.agent --dry-run            # who would be contacted
python -m emailer.agent                      # write .eml drafts to outreach_drafts/
python -m emailer.agent --only-manufacturers # only labs that synthesize their own
python -m emailer.agent --limit 10           # cap this batch
```

**Drafts are the default and nothing is sent.** Review the `.eml` files, then
either send them from your mail client or re-run with:

```bash
python -m emailer.agent --send --i-understand-this-sends-real-email
```

Sending requires `SMTP_*` credentials in `.env`, and refuses to run until
`SENDER_EMAIL` and `SENDER_POSTAL_ADDRESS` are filled in — US commercial
email must carry a real physical address and a working opt-out under
CAN-SPAM, and both are rendered into the message footer. Messages are spaced
`EMAIL_DELAY_SECONDS` apart so a batch doesn't leave as a burst.

Every message is written to the `outreach` table, and the agent skips any
lead that already has a row there. Re-running is safe: it only ever contacts
companies added since last time, and the lead's status flips to `contacted`.
The CRM's lead page shows the outreach history.

## Running it continuously

`pipeline.py` does discovery then outreach in one go. Both halves skip work
they've already done, so it's safe on a repeating schedule.

```bash
python pipeline.py                  # discover new vendors + write drafts
python pipeline.py --seeds-only     # re-check the seed list, no search spend
python pipeline.py --skip-scrape    # outreach only
```

Daily at 9am via cron:

```
0 9 * * * cd /path/to/repo && .venv/bin/python pipeline.py >> pipeline.log 2>&1
```

Leave off `--send` and each run just stacks reviewable drafts in
`outreach_drafts/`.

## Importing a vendor list by hand

Some directories block automated access. When you can view one in your
browser but a script can't fetch it, copy the page (or save it) and run:

```bash
python -m scraper.import_list saved-page.txt --source-host thedirectory.com
```

That pulls out company domains and appends them to `scraper/seed_urls.txt`.

If a listing gives company *names* but only links to profile pages on the
directory's own domain, put the names in a TSV (`name<TAB>country`, see
`scraper/vendor_names.tsv`) and resolve them to real websites:

```bash
python -m scraper.resolve_vendors --country "United States" --dry-run
python -m scraper.resolve_vendors --country "United States"
```

One search per name, scored by how well each candidate domain matches the
company name, so directories and blogs don't get through.

---

# The clinics agent: 400 new US peptide clinics a day

`clinics/agent.py` is the second discovery agent. It looks for practices that
*administer* peptides rather than companies that sell them, and it is built
around a daily number rather than a query budget.

```bash
# See what it finds without writing anything
python -m clinics.agent --dry-run -v --max-queries 10

# A normal day: keep going until 400 new clinics are in the CRM
python -m clinics.agent --daily-target 400

# Narrow to particular states
python -m clinics.agent --daily-target 400 --state TX,FL,AZ --workers 12
```

## Clinic types

Each clinic is bucketed by what its own site leans into hardest, ties
breaking toward the more specific practice:

- `hormone_clinic` — HRT/TRT, bioidentical hormones, menopause/andropause
- `regenerative_clinic` — regenerative and sports medicine, PRP, exosomes
- `weight_loss_clinic` — medical weight loss, GLP-1 programs
- `med_spa` — aesthetics-led practices (Botox, fillers, lasers)
- `wellness_clinic` — functional/integrative/anti-aging/longevity, and the
  catch-all for a peptide-offering practice that leans into none of the above

Clinics that treat patients remotely are also flagged `telehealth`, which is
a different supply conversation than a single-location practice.

A site is only saved as a clinic lead if all of these hold:

1. it reads as a practice that treats patients (booking prompts, provider
   language — two independent signals, so a stray "ask your clinic" doesn't
   count),
2. it offers peptides — either therapy wording of its own ("peptide
   therapy", "peptide injections") or a tracked compound named in clinic
   context,
3. it isn't a research-chemical storefront (those are the vendor scraper's
   target and use the same compound names), and
4. it looks US-based (`--allow-non-us` to disable).

Directory, marketplace and social domains (Yelp, Healthgrades, Zocdoc,
Facebook, ...) are dropped before a page is ever fetched — they rank well for
local searches but are never the clinic's own site.

## How it keeps finding new ones every day

Clinics are local businesses, so every query is geo-targeted: each of the
~450 cities in `clinics/us_cities.txt` is crossed with each service in
`clinics/clinic_services.txt` and each template in
`clinics/query_templates.py`. That's a query space of ~90,000 distinct
searches, walked in city-major order so a partial day still samples the whole
country.

The agent saves its position in that space (`discovery_state` table) and the
next run **resumes where the last one stopped**. Without that, every day would
re-run the same first few hundred searches and rediscover the same clinics.

The daily target counts clinic leads added **today** (UTC), not this run — so
if a run dies halfway, the next one tops the day up to 400 instead of adding
another 400. Once the day's number is met, an extra run is a no-op.

Reaching 400 means visiting a few thousand candidate sites, which a
sequential crawl won't finish in a day, so sites are fetched concurrently
(`--workers`, default 8). Each site still gets its own pages fetched in
sequence, robots.txt is still respected, and no two workers ever touch the
same domain.

Flags:
- `--daily-target N` — new clinic leads to reach today (default 400; `0` for
  no target, i.e. run until the query budget is spent)
- `--max-queries N` — hard cap on searches this run (default 600) so a bad day
  can't burn a whole month of API quota. Counts logical searches; each is sent
  to every configured provider
- `--query-batch N` — searches between target re-checks (default 25)
- `--workers N` — concurrent site fetches (default 8)
- `--state TX,FL` — limit discovery to particular states
- `--no-directory` — skip Google Places discovery
- `--restart-cursor` — start again from the top of the query space
- `--allow-non-us`, `--dry-run`, `-v`

### What 400/day actually costs

400 new leads means roughly 300–600 searches and a few thousand site visits
per day, and clinics get discovered once — the same city/service pair
mostly returns clinics you already have, which is exactly why the cursor
keeps moving. In practice:

- **Free search tiers won't do it.** Google CSE's free tier is 100
  queries/day. Serper or Brave on a paid tier is the realistic setup; the
  agent uses every configured provider for each query, so two providers
  double the results per search rather than doubling the search count.
- **Google Places is worth configuring** for this agent specifically. Clinics
  are physical local businesses, and Places finds practices with no
  search-engine footprint at all. The agent spends up to a fifth of its query
  budget there, rotating cities so consecutive days cover different metros.
- **Yield falls over time.** After a few months of daily runs you'll have
  most of the addressable market; when `duplicate` starts dominating the run
  stats, add cities or services rather than raising the target.

## Running the clinic pipeline daily

`clinic_pipeline.py` does discovery, reply detection, then outreach — the
clinic equivalent of `pipeline.py`, and safe to re-run.

```bash
python clinic_pipeline.py                     # 400 new clinics + drafts
python clinic_pipeline.py --daily-target 100  # smaller day
python clinic_pipeline.py --skip-scrape       # outreach only
```

On a cron, every day at 7am:

```
0 7 * * *  cd /path/to/repo && .venv/bin/python clinic_pipeline.py >> clinic_pipeline.log 2>&1
```

Early start is deliberate: discovery is the slow half, so it leaves the whole
day to reach 400.

Clinic outreach uses `emailer/clinic_message.txt` and
`emailer/clinic_followup.txt` — separate copy from the vendor pitch, since a
clinic is buying to treat patients rather than to resell. Edit both freely;
the first line is the `Subject:`. The same cadence, dedup, opt-out handling
and CAN-SPAM footer requirements apply to both campaigns, and the emailer
picks each lead's copy from its `kind`, so a clinic can never be sent the
vendor pitch by accident.

Sending to clinics is regulated the same way as any other US commercial
email, and what you may say about supplying peptides to a practice is subject
to rules the code can't check for you. Read the copy before you send it.
