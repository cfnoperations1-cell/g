# Peptide Lead Bot — instructions for Claude Code

This repo is a lead-generation scraper. Three modes share one pipeline:
- `medspa` — med spas that offer peptides (local, city-by-city)
- `clinic` — wellness / hormone / longevity clinics offering peptides (local)
- `vendor` — peptide suppliers, 503A/503B pharmacies, wholesalers (national)

Pipeline: **discover** (search APIs + directory pages → candidates in SQLite) → **enrich** (visit each site, extract emails/phones/IG, confirm peptide keywords) → **export** (CSV in `data/out/`).

## Setup (do this first)
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env      # then add GOOGLE_PLACES_API_KEY and/or SERPER_API_KEY
```
If the user has no API keys, the bot still works via DuckDuckGo but is slower and finds fewer med spas. Tell them Places is the single biggest quality lever (~$32 per 1,000 text-search calls; the default config is ~300 calls per local mode).

## Commands
```bash
python -m bot run --mode medspa                     # full run, all cities in config/queries.yaml
python -m bot run --mode clinic
python -m bot run --mode vendor
python -m bot run --mode medspa --cities "Las Vegas, NV;Henderson, NV" --limit 5   # quick test
python -m bot status                                # counts per mode
python -m bot export --mode medspa                  # re-export CSVs without re-scraping
python -m bot login thepeptidelist.com              # capture a session for a gated directory
```
Runs are resumable — rerun the same command and it skips finished queries and already-enriched sites. Delete `data/leads.sqlite` to start fresh.

## Output CSVs (data/out/)
- `<mode>_all_<date>.csv` — everything found
- `<mode>_peptide_confirmed_<date>.csv` — site text actually mentions peptide terms (use this one)
- `<mode>_peptide_with_email_<date>.csv` — confirmed AND has a real published email

Columns: business_name, category, website, emails, phones, address, city, state, zip, rating, review_count, google_maps_url, instagram, peptides_found, peptide_hits, confidence, contact_page, source, source_query, notes.

## Rules for editing this bot
- Never fabricate emails. An empty `emails` cell means the site publishes none; leave it empty.
- Never add automated account creation. Gated sites → `python -m bot login <domain>` (human logs in once, session reused).
- Keep `REQUEST_DELAY_SECONDS` ≥ 1. Getting IP-banned kills the whole run.
- Add new search phrasings / cities / directory URLs in `config/queries.yaml`, not in code.
- Add new peptide names to `bot/keywords.py` `PEPTIDE_TERMS`.
- If a directory site blocks Chromium too, set `HEADLESS=false` in `.env` so a visible browser is used.

## Where things live
- `bot/discover.py` — Google Places / Serper / DuckDuckGo search
- `bot/enrich.py` — site crawl, email/phone extraction, Playwright fallback, directory crawling
- `bot/keywords.py` — peptide term list + med spa / clinic / vendor classifier
- `bot/store.py` — SQLite + CSV export
- `bot/session.py` — manual login capture
