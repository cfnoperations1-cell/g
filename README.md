# Peptide Research Lead-Gen: Scraper Agent + CRM

A two-part system for finding companies that sell "research use only" peptides
and tracking outreach to them:

1. **Scraper agent** (`scraper/`) — discovers candidate companies via search
   and business-directory APIs, visits each company's own public website,
   checks for "research use only" language, and saves qualifying companies
   as leads.
2. **CRM** (`crm/`) — a small local Flask + SQLite web app to view leads,
   filter by status, and track outreach notes.

Both share one SQLite database (`data/leads.db`) via `db.py` / `models.py`.

An **email follow-up agent** is planned as phase 2 (see Roadmap below) — it
is intentionally not built yet.

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

- **Google Custom Search** (recommended first choice): create a search
  engine at https://programmablesearchengine.google.com/ (set it to search
  the entire web), then create an API key at
  https://console.cloud.google.com/apis/credentials. Fill in
  `GOOGLE_CSE_API_KEY` and `GOOGLE_CSE_CX`.
- **Bing Web Search API**: create an Azure Cognitive Services "Bing Search
  v7" resource and use its key as `BING_SEARCH_API_KEY`.
- **Google Places API** (optional, directory-based discovery): enable
  "Places API" in Google Cloud Console and set `GOOGLE_PLACES_API_KEY`.

Any provider left blank is simply skipped at runtime (you'll see a log line
saying so) — the agent still runs with whatever you've configured.

## Running the scraper agent

```bash
# Dry run first: prints what it would find, writes nothing to the DB
python -m scraper.agent --dry-run -v

# Real run: populates data/leads.db
python -m scraper.agent --per-query 15 --limit 50
```

Flags:
- `--queries-file PATH` — override the default query list (`scraper/queries.txt`)
- `--per-query N` — results to request per query, per provider (default 10)
- `--limit N` — stop after visiting N new candidate sites
- `--allow-non-research` — keep leads even if their site has no "research
  use only" language (by default, only research-use-only sellers are kept)
- `--dry-run` — print results without writing to the database
- `-v` — verbose logging

Edit `scraper/queries.txt` to tune what it searches for — one query per
line, `#` for comments.

## Running the CRM

```bash
python run_crm.py
# open http://127.0.0.1:5000
```

View all leads, filter by status (new / contacted / replied / qualified /
disqualified), open a lead to see extracted contact info and the "research
use only" evidence snippet that qualified it, and add notes.

## Running tests

```bash
pytest
```

Tests cover the pure logic (email extraction, research-only phrase
detection, dedup/upsert rules) with no network calls or external services.

## Project layout

```
config.py, db.py, models.py     # shared config + SQLAlchemy engine + Lead model
scraper/
  search_providers.py           # Google CSE / Bing search API wrappers
  directory_providers.py        # Google Places directory API wrapper
  site_parser.py                # fetches a company's site, extracts contact info
  agent.py                      # orchestrates: search -> visit -> filter -> save
  queries.txt                   # editable list of discovery queries
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
