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
