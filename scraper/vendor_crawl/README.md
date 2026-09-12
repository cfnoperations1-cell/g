# Vendor email crawl

Visits every known US peptide vendor's own website, pulls published emails/phones,
checks for peptide terms + US signals, and writes `data/out/us_peptide_vendors_*.csv`.
Inputs: `scraper/peptide_vendors_master.csv` (master roster), `scraper/vendor_domains.tsv`,
`scraper/seed_urls.txt`. Work files live in `data/vendor_crawl/` (`VENDOR_CRAWL_DIR` to override).

```bash
# 1. domains.tsv = one line per site: domain<TAB>source<TAB>name  (build from the roster files)
python scraper/vendor_crawl/crawl.py data/vendor_crawl/domains.tsv          # requests pass, resumable
# 2. vendors with a name but no website: try name-derived domains and verify on-page
python scraper/vendor_crawl/guess.py names.txt data/vendor_crawl/guessed.tsv
# 3. Cloudflare / JS-only sites: headless Chromium, N shards
python scraper/vendor_crawl/pw_pass.py data/vendor_crawl/results.jsonl 0 4   # shard 0 of 4
# 3b. reachable sites with no email yet: every policy/contact path, then Chromium (3 shards)
python scraper/vendor_crawl/deep_pass.py 0 3
# 4. merge everything into the CSVs
python scraper/vendor_crawl/compile_csv.py 2026-09-10
```

Rules: robots.txt is honoured (a 4xx robots.txt counts as "allowed"), one request per
second per site, Chrome-like User-Agent, no search-engine result pages are scraped, and
no emails are ever guessed. Behind a TLS-intercepting proxy, Chromium needs
`--disable-features=PostQuantumKyber,UseMLKEM,EncryptedClientHello --ssl-version-max=tls1.2`
(already set in `pw_pass.py`) and `CHROMIUM_PATH` if Playwright's own browser isn't installed.

Discovery: `search_browser.py` runs the query list through headless Chromium on Bing into `search_cache.json`; `discover.py` then harvests vendor links from coupon/list pages into `domains_disc.tsv`.

## Med spas / clinics (no API key)

Bing web search geolocates to the server's region and returns articles for "med spa peptides <city>"; Startpage (Google results)
rendered in headless Chromium returns the actual local businesses. Flow:

```bash
python scraper/vendor_crawl/sp_search.py scraper/vendor_crawl/medspa_queries.txt data/vendor_crawl/medspa_cache_sp.json
MEDSPA_CACHE=data/vendor_crawl/medspa_cache_sp.json python scraper/vendor_crawl/medspa_domains.py   # result sites -> domains.tsv with state|city
MEDSPA_MODE=1 MAX_LINKED=10 CRAWL_OUT=data/vendor_crawl/medspa/results_sp.jsonl python scraper/vendor_crawl/crawl.py data/vendor_crawl/medspa/domains.tsv
python scraper/vendor_crawl/pw_pass.py data/vendor_crawl/medspa/results_sp.jsonl 0 4     # x4 shards
python scraper/vendor_crawl/medspa_compile.py 2026-09-12   # data/out/medspa_peptide_*.csv + medspa_by_state_<date>/ + state summary
```
`MEDSPA_MODE` makes the crawler prioritise service / treatment / weight-loss / peptide pages, where med spas list what they offer.
