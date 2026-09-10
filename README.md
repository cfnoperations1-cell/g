# Peptide Industry Lead-Gen: Scraper + CRM + Outreach

A three-part system for finding US-based companies in the peptide industry
and running outreach to them:

1. **Scraper agent** (`scraper/`) — discovers candidate companies via search
   and business-directory APIs, visits each company's own public website,
   classifies it into a target company type, guesses whether it's US-based,
   and saves qualifying companies as leads.
2. **CRM** (`crm/`) — a small local Flask + SQLite web app to view leads,
   filter by status and company type, and track outreach notes.
3. **Emailer agent** (`emailer/`) — writes outreach to leads that have an
   email address, records every message so nobody is contacted twice, and
   defaults to writing drafts for review rather than sending.

All three share one SQLite database (`data/leads.db`) via `db.py` /
`models.py`. `pipeline.py` runs the scraper and emailer back to back, which
is what you schedule to keep adding vendors continuously.

## Target company types

Every lead is classified into one of four categories, based on what its own
site says:

- `research_only` — sells peptides labeled "for research use only" / "not
  for human consumption"
- `consumer_and_research` — sells peptides with no research-only
  restriction (i.e. to consumers as well as researchers)
- `compounding_pharmacy` — a licensed compounding pharmacy (detected via
  phrases like "compounding pharmacy", "PCAB accredited", "USP 795/797")
- `manufacturing_lab` — a peptide manufacturing/synthesis lab (detected via
  phrases like "cGMP", "custom peptide synthesis", "API manufacturer")
- `med_spa` — a med spa / aesthetics practice that offers peptides to its
  clients (a buyer, not a seller; detected via practice copy such as
  "Botox", "fillers", "microneedling")
- `clinic` — a wellness / hormone / longevity clinic offering peptide
  therapy (also a buyer; "hormone therapy", "IV therapy", "book a consultation")

A site only gets classified at all if it actually mentions one of your
tracked peptide keywords (`scraper/peptide_keywords.txt`) — otherwise it's
skipped as irrelevant. By default, only leads that also look US-based are
kept (see below); pass `--allow-non-us` to disable that filter.

`med_spa` and `clinic` are found **city by city**: their query templates
take a `{city}` placeholder filled from `scraper/cities.txt` (or
`--cities "Las Vegas, NV;Phoenix, AZ"`), and Google Places is by far the
best source for them because it returns the business's website, phone and
address in one call. Because they buy peptides rather than sell them, the
"must be a storefront" filter doesn't apply to these two types.

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

**No keys at all?** The agent falls back to DuckDuckGo through the `ddgs`
package (installed with the requirements). It needs no account, but it is
slow, rate-limited and noisier than a real API, so treat it as a way to get
going rather than the way to run at volume. Set `DUCKDUCKGO_FALLBACK=false`
to turn it off.

### Finnrick (finnrick.com)

Finnrick publishes independent test results for peptide vendors together
with each vendor's contact channels (website, email, WhatsApp, Telegram),
and its `robots.txt` explicitly allows reading the public API under
`/api/v1/`. `scraper/finnrick.py` reads that index (cached for a day in
`data/finnrick_vendors.json`) and is the first source `enrich_csv` consults.

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

### JavaScript-only sites (headless browser fallback)

Some vendor sites ship an empty HTML shell and render everything in the
browser, or answer a plain request with a bot-challenge page. When a fetch
comes back like that and Playwright is installed, the page is re-fetched
in headless Chromium, with the same User-Agent and the same robots.txt
decision as the plain request. Install it once:

```bash
pip install playwright && playwright install chromium
```

It is on whenever Playwright is importable; `BROWSER_FALLBACK=false`
disables it, and `PLAYWRIGHT_CHROMIUM_PATH` points it at an existing
Chromium binary if you have one.

## Running the scraper agent

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
- `--cities` — semicolon-separated cities for `med_spa` / `clinic` queries
  (default: every line of `scraper/cities.txt`)
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

View all leads, filter by status (new / contacted / replied / qualified /
disqualified) and by company type, open a lead to see extracted contact
info, detected state, and the classification evidence, and add notes.

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
  search_providers.py           # Google CSE / Serper / Brave / Bing APIs + DuckDuckGo fallback
  directory_providers.py        # Google Places (New) text search
  finnrick.py                   # Finnrick public vendor API (contact channels)
  finnrick_roster.py            # adds Finnrick's vendors to a spreadsheet
  peptiprices_roster.py         # adds suppliers linked from peptiprices.com
  enrich_csv.py                 # completes a vendor spreadsheet from the sources above
  cities.txt                    # {city} values for the med_spa / clinic templates
  site_parser.py                # fetches a company's site, extracts contact info
  classify.py                   # company-type classification + US-presence heuristic
  query_templates.py            # per-company-type search query templates
  peptide_keywords.txt          # editable list of tracked peptides/compounds
  agent.py                      # orchestrates: search -> visit -> classify -> save
crm/
  routes.py, templates/, static/  # Flask views for browsing/updating leads
run_crm.py                      # CRM entrypoint
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

## Completing a vendor spreadsheet

If you already have a list of vendors with gaps in it (names, some
directory links, few websites, fewer emails), `enrich_csv` fills the blanks
from sources that publish the vendor's own details and writes a completed
copy. Nothing is ever invented: a cell no source can fill stays empty,
existing values are never overwritten, and each row says where every fill
came from.

```bash
python -m scraper.enrich_csv vendors.csv                       # -> data/out/vendors_enriched.csv
python -m scraper.enrich_csv vendors.csv --out exports/vendors_enriched.csv --import-crm
python -m scraper.enrich_csv vendors.csv --no-visit            # directory sources only
python -m scraper.enrich_csv vendors.csv --limit 20            # quick test
python -m scraper.enrich_csv vendors.csv --resume              # continue an interrupted run
```

Sources, in order: Finnrick's public vendor API (website, email, WhatsApp,
Telegram), `scraper/vendor_domains.tsv` (websites resolved earlier), a web
search for the name (keyed API or the DuckDuckGo fallback), and finally
the vendor's own site (email, phone, Instagram, whether it actually
mentions peptides, and the company type). The input needs only a
`Vendor` / `Name` / `Company` column; these columns are filled or added:

`Website, Email, Phone, WhatsApp, Telegram/Signal, Social, Email Source,
Finnrick Profile` plus `Website Source, Site Status, Peptide Confirmed,
Company Type, Detected US State, Instagram, Enrichment, Enriched At`.

`--import-crm` also loads every row that has a website into the CRM as a
lead (source `vendor_csv`), filling blanks on leads that already exist, so
the outreach agent can pick them up. `--workers 6` processes six vendors
at a time (each site is still crawled one page at a time), and
`--skip-visit-when-email` skips the crawl for rows that already have an
email, which makes a roster of a couple of thousand names finish in well
under an hour.

### Growing the roster from the directories

Two commands add vendors you don't have yet, ready for `enrich_csv`:

```bash
python -m scraper.finnrick_roster vendors.csv --out combined.csv              # every live Finnrick vendor
python -m scraper.finnrick_roster vendors.csv --out combined.csv --only-tested
python -m scraper.peptiprices_roster combined.csv --out combined.csv         # suppliers linked from peptiprices.com
python -m scraper.enrich_csv combined.csv --out exports/combined_enriched.csv --workers 6 --import-crm
```

Finnrick's index carries roughly 1,800 vendors (name, location, website,
trading status, number of tests); vendors marked not found, deactivated or
archived are skipped and tested vendors are added first. PeptiPrices links
each supplier's own site from its `/suppliers` page. PeptideBase sits
behind a bot challenge and is not read.

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
