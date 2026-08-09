# Peptide Industry Lead-Gen: Scraper Agent + CRM

A two-part system for finding US-based companies in the peptide industry
and tracking outreach to them:

1. **Scraper agent** (`scraper/`) — discovers candidate companies via search
   and business-directory APIs, visits each company's own public website,
   classifies it into a target company type, guesses whether it's US-based,
   and saves qualifying companies as leads.
2. **CRM** (`crm/`) — a small local Flask + SQLite web app to view leads,
   filter by status and company type, and track outreach notes.

Both share one SQLite database (`data/leads.db`) via `db.py` / `models.py`.

An **email follow-up agent** is planned as phase 2 (see Roadmap below) — it
is intentionally not built yet.

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

A site only gets classified at all if it actually mentions one of your
tracked peptide keywords (`scraper/peptide_keywords.txt`) — otherwise it's
skipped as irrelevant. By default, only leads that also look US-based are
kept (see below); pass `--allow-non-us` to disable that filter.

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

- **Google Custom Search** (recommended first choice, free tier = 100
  queries/day):
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
  search_providers.py           # Google CSE / Bing search API wrappers
  directory_providers.py        # Google Places directory API wrapper
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

## Roadmap: email follow-up agent (phase 2)

Not built yet, by design — get the scraper and CRM working first. When
ready, the plan is:
- Use the Gmail connector to **draft** (not auto-send) a personalized
  follow-up email per `new` lead, then flip its status to `contacted`.
- Only ever create drafts for review first, until you're comfortable
  trusting the message quality enough to consider auto-send.
- Pull reply detection from Gmail threads to flip `contacted` -> `replied`.
