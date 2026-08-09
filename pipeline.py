"""Run the full loop: find new vendors, then prepare outreach for them.

Built for a schedule, and safe to run daily. Each step only does work that
is actually outstanding:
  1. discovery skips domains already in the CRM
  2. reply detection drops anyone who answered out of the cadence
  3. outreach sends the intro to new leads, and a follow-up to anyone whose
     last message was more than --interval-days ago

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

import config
from emailer import agent as emailer_agent
from emailer import replies as emailer_replies
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
    parser.add_argument("--interval-days", type=int, default=config.FOLLOWUP_INTERVAL_DAYS)
    parser.add_argument("--max-followups", type=int, default=config.MAX_FOLLOWUPS)
    parser.add_argument("--skip-reply-check", action="store_true", help="Don't scan the inbox for replies")
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

    # Check for replies BEFORE sending, so anyone who answered since the last
    # run drops out of the cadence instead of getting another follow-up.
    if not args.skip_reply_check:
        if config.IMAP_HOST and config.IMAP_USERNAME:
            logger.info("=== step 2: checking for replies ===")
            try:
                logger.info("replies: %s", emailer_replies.run(since_days=30, dry_run=False))
            except Exception as exc:
                logger.warning("reply check failed (continuing): %s", exc)
        else:
            logger.info("=== step 2: skipped, IMAP not configured ===")
            logger.info("   mark leads 'replied' in the CRM by hand to stop their follow-ups")

    logger.info("=== step 3: preparing outreach ===")
    email_stats = emailer_agent.run(
        message_file=emailer_agent.DEFAULT_MESSAGE_FILE,
        drafts_dir=emailer_agent.DEFAULT_DRAFTS_DIR,
        only_manufacturers=args.only_manufacturers,
        limit=args.email_limit,
        send=args.send,
        dry_run=False,
        interval_days=args.interval_days,
        max_followups=args.max_followups,
    )
    logger.info("outreach: %s", email_stats)


if __name__ == "__main__":
    main()
