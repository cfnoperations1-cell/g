"""Detect replies from leads and stop their follow-up sequence.

Connects to your inbox over IMAP (read-only), looks at recent senders, and
matches them against the leads you have contacted. A match sets the lead's
replied_at, which is what makes the cadence stop -- otherwise follow-ups
keep going out to someone who already answered.

Matching is by email domain rather than exact address, so a reply from
jane@acme.com counts for a lead contacted at sales@acme.com.

    python -m emailer.replies --dry-run
    python -m emailer.replies --since-days 30

Run it before the outreach step; pipeline.py already does.
"""
from __future__ import annotations

import argparse
import email
import imaplib
import logging
import re
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Dict, List, Optional, Set

import config
from db import SessionLocal, init_db
from models import Lead, Outreach

logger = logging.getLogger(__name__)

OPT_OUT_PATTERNS = re.compile(
    r"\b(unsubscribe|opt[\s-]?out|remove me|stop emailing|do not (?:contact|email))\b",
    re.IGNORECASE,
)


def email_domain(address: str) -> str:
    domain = address.partition("@")[2].lower().strip()
    return domain[4:] if domain.startswith("www.") else domain


def contacted_domain_map(session) -> Dict[str, List[Lead]]:
    """Map recipient email domain -> leads we contacted at that domain."""
    mapping: Dict[str, List[Lead]] = {}
    rows = (
        session.query(Outreach.lead_id, Outreach.to_email)
        .filter(Outreach.delivery != "failed")
        .distinct()
    )
    lead_ids = {lead_id for lead_id, _ in rows}
    leads = {lead.id: lead for lead in session.query(Lead).filter(Lead.id.in_(lead_ids))} if lead_ids else {}

    for lead_id, to_email in rows:
        lead = leads.get(lead_id)
        if lead is None:
            continue
        mapping.setdefault(email_domain(to_email), []).append(lead)
    return mapping


def _plain_text(msg) -> str:
    """Best-effort plain-text body, whatever the message structure."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode("utf-8", "replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode("utf-8", "replace")


def fetch_recent_senders(since_days: int) -> List[tuple]:
    """[(from_address, subject, body_snippet)] from the inbox, newest first."""
    if not (config.IMAP_HOST and config.IMAP_USERNAME):
        raise RuntimeError("IMAP_HOST / IMAP_USERNAME are not set in .env")

    since = (datetime.utcnow() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    results = []

    with imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT) as imap:
        imap.login(config.IMAP_USERNAME, config.IMAP_PASSWORD)
        imap.select(config.IMAP_FOLDER, readonly=True)
        status, data = imap.search(None, f'(SINCE {since})')
        if status != "OK":
            return results

        for num in data[0].split():
            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            sender = parseaddr(msg.get("From", ""))[1]
            subject = msg.get("Subject", "")
            body = _plain_text(msg)
            results.append((sender, subject, body[:2000]))

    return results


def apply_replies(session, messages: List[tuple], dry_run: bool) -> dict:
    """Mark leads replied (and opted out where they asked) from inbox messages."""
    domain_map = contacted_domain_map(session)
    stats = {"replies": 0, "opt_outs": 0, "unmatched": 0}
    seen: Set[int] = set()

    for sender, subject, body in messages:
        domain = email_domain(sender)
        leads = domain_map.get(domain)
        if not leads:
            stats["unmatched"] += 1
            continue

        wants_out = bool(OPT_OUT_PATTERNS.search(f"{subject}\n{body}"))

        for lead in leads:
            if lead.id in seen:
                continue
            seen.add(lead.id)

            if dry_run:
                logger.info(
                    "[dry-run] %s from %s -> %s",
                    "OPT-OUT" if wants_out else "reply", sender, lead.company_name,
                )
            else:
                if lead.replied_at is None:
                    lead.replied_at = datetime.utcnow()
                lead.status = "replied"
                if wants_out:
                    lead.opted_out = True
                logger.info(
                    "%s from %s -> %s (follow-ups stopped)",
                    "OPT-OUT" if wants_out else "reply", sender, lead.company_name,
                )

            stats["opt_outs" if wants_out else "replies"] += 1

    if not dry_run:
        session.commit()
    return stats


def run(since_days: int, dry_run: bool) -> dict:
    init_db()
    session = SessionLocal()
    try:
        messages = fetch_recent_senders(since_days)
        logger.info("scanned %d inbox messages from the last %d days", len(messages), since_days)
        stats = apply_replies(session, messages, dry_run)
    finally:
        session.close()
    logger.info("Done. %s", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since-days", type=int, default=30, help="How far back to scan (default 30)")
    parser.add_argument("--dry-run", action="store_true", help="Report matches without changing anything")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run(args.since_days, args.dry_run)


if __name__ == "__main__":
    main()
