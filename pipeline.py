"""Run the full loop: find new vendors, then prepare outreach for them.

Built for a schedule. The scraper skips domains already in the CRM and the
emailer skips leads already contacted, so running this repeatedly only ever
acts on genuinely new companies.

    python pipeline.py                    # discover + write drafts
    python pipeline.py --seeds-only       # re-check the seed list, no search
    python pipeline.py --send --i-understand-this-sends-real-email

Schedule it (every day at 9am):
    0 9 * * *  cd /path/to/repo && .venv/bin/python pipeline.py >> pipeline.log 2>&1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from emailer import agent as emailer_agent
from scraper import agent as scraper_agent

logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).parent / "scraper" / "seed_urls.txt"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-queries", type=int, default=20, help="Search budget for the discovery step")
    parser.add_argument("--per-query", type=int, default=10)
    parser.add_argument("--seeds-only", action="store_true", help="Skip search discovery; only re-check seed URLs")
    parser.add_argument("--skip-scrape", action="store_true", help="Only run outreach on leads already in the CRM")
    parser.add_argument("--only-manufacturers", action="store_true")
    parser.add_argument("--email-limit", type=int, default=None, help="Cap messages per run")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--i-understand-this-sends-real-email", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.send and not args.i_understand_this_sends_real_email:
        sys.exit("--send requires --i-understand-this-sends-real-email")

    if not args.skip_scrape:
        logger.info("=== step 1: discovering vendors ===")
        scrape_stats = scraper_agent.run(
            keywords_file=scraper_agent.DEFAULT_KEYWORDS_FILE,
            per_query=args.per_query,
            limit=None,
            max_queries=args.max_queries,
            company_types=scraper_agent.DEFAULT_COMPANY_TYPES,
            allow_non_us=False,
            dry_run=False,
            seed_urls_file=SEED_FILE if args.seeds_only else None,
        )
        logger.info("discovery: %s", scrape_stats)

    logger.info("=== step 2: preparing outreach ===")
    email_stats = emailer_agent.run(
        message_file=emailer_agent.DEFAULT_MESSAGE_FILE,
        drafts_dir=emailer_agent.DEFAULT_DRAFTS_DIR,
        only_manufacturers=args.only_manufacturers,
        limit=args.email_limit,
        send=args.send,
        dry_run=False,
    )
    logger.info("outreach: %s", email_stats)


if __name__ == "__main__":
    main()
