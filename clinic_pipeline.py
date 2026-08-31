"""The daily clinic loop: find 400 new US peptide clinics, then work outreach.

This is the thing you put on a cron. Each step only does what is actually
outstanding, so a re-run after a failure tops up the day rather than doubling
it:

  1. discovery adds new clinic leads until the day's target is met, skipping
     domains already in the CRM and resuming its query cursor from where the
     last run stopped
  2. reply detection drops anyone who answered out of the follow-up cadence
  3. outreach sends the clinic copy to new clinic leads, and a follow-up to
     any clinic whose last message was more than --interval-days ago

    python clinic_pipeline.py                     # 400 new clinics + drafts
    python clinic_pipeline.py --daily-target 100  # smaller day
    python clinic_pipeline.py --skip-scrape       # outreach only
    python clinic_pipeline.py --send --i-understand-this-sends-real-email

Every day at 7am:
    0 7 * * *  cd /path/to/repo && .venv/bin/python clinic_pipeline.py >> clinic_pipeline.log 2>&1

The 7am slot is deliberate: discovery is the slow half (a few thousand site
visits), so starting early leaves the whole day for it to reach 400.
"""
from __future__ import annotations

import argparse
import logging
import sys

import config
from clinics import agent as clinics_agent
from emailer import agent as emailer_agent
from emailer import replies as emailer_replies

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--daily-target", type=int, default=clinics_agent.DEFAULT_DAILY_TARGET,
        help=f"New clinic leads to reach today (default {clinics_agent.DEFAULT_DAILY_TARGET})",
    )
    parser.add_argument("--max-queries", type=int, default=600, help="Search budget for the discovery step")
    parser.add_argument("--per-query", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8, help="Concurrent site fetches")
    parser.add_argument("--state", type=str, default=None, help="Limit discovery to these states, e.g. TX,FL")
    parser.add_argument("--skip-scrape", action="store_true", help="Only run outreach on clinics already in the CRM")
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
        logger.info("=== step 1: discovering clinics (target %d today) ===", args.daily_target)
        logger.info("discovery: %s", clinics_agent.run(
            daily_target=args.daily_target or None,
            per_query=args.per_query,
            max_queries=args.max_queries,
            workers=args.workers,
            state_filter=args.state,
            dry_run=False,
        ))

    # Check replies BEFORE sending, so a clinic that answered since the last
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
            logger.info("   mark clinics 'replied' in the CRM by hand to stop their follow-ups")

    logger.info("=== step 3: preparing clinic outreach ===")
    logger.info("outreach: %s", emailer_agent.run(
        kind="clinic",
        message_file=None,
        drafts_dir=emailer_agent.DEFAULT_DRAFTS_DIR,
        only_manufacturers=False,
        limit=args.email_limit,
        send=args.send,
        dry_run=False,
        interval_days=args.interval_days,
        max_followups=args.max_followups,
    ))


if __name__ == "__main__":
    main()
