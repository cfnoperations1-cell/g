"""Outreach agent: emails peptide vendor leads the scraper agent found.

Reads leads from the shared CRM database and works a follow-up cadence: an
intro message, then a follow-up every few days until the company replies.
Every message is recorded, so running this on a schedule behind a scraper
that keeps adding vendors only ever sends what is actually due.

The sequence stops for a lead as soon as any of these is true:
  * a reply is detected (emailer.replies) or their status is set to replied
  * they ask to opt out (Lead.opted_out)
  * the follow-up cap is reached (--max-followups, default 4)

Two modes:
  drafts (default) -- writes .eml files to outreach_drafts/ for you to review
                      and send yourself. Nothing leaves the machine.
  --send           -- delivers via SMTP. Requires SMTP_* settings in .env and
                      an explicit --i-understand-this-sends-real-email flag.

Usage:
    python -m emailer.agent --dry-run              # show who would be contacted
    python -m emailer.agent                        # write drafts to review
    python -m emailer.agent --only-manufacturers   # narrow to labs that synthesize
    python -m emailer.agent --interval-days 3 --max-followups 4
    python -m emailer.agent --send --i-understand-this-sends-real-email
"""
from __future__ import annotations

import argparse
import logging
import smtplib
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import List, Optional, Tuple

import config
from db import SessionLocal, init_db
from models import Lead, Outreach

logger = logging.getLogger(__name__)

DEFAULT_MESSAGE_FILE = Path(__file__).parent / "message.txt"
DEFAULT_FOLLOWUP_FILE = Path(__file__).parent / "followup.txt"
DEFAULT_DRAFTS_DIR = Path(config.BASE_DIR) / "outreach_drafts"

# A lead in any of these states is finished -- never contact them again.
TERMINAL_STATUSES = {"replied", "qualified", "disqualified"}


def load_message(path: Path) -> Tuple[str, str]:
    """Read the template file into (subject, body).

    The first line must be "Subject: ..."; everything after the blank line
    that follows it is the body.
    """
    text = path.read_text()
    lines = text.splitlines()
    if not lines or not lines[0].lower().startswith("subject:"):
        raise ValueError(f"{path} must start with a 'Subject:' line")
    subject = lines[0].split(":", 1)[1].strip()
    body = "\n".join(lines[1:]).lstrip("\n")
    return subject, body


def render(body: str) -> str:
    return body.format(
        sender_name=config.SENDER_NAME,
        sender_email=config.SENDER_EMAIL,
        sender_company=config.SENDER_COMPANY,
        sender_postal_address=config.SENDER_POSTAL_ADDRESS,
        unsubscribe_line=config.UNSUBSCRIBE_LINE,
    )


def compliance_problems() -> List[str]:
    """Settings that must be filled in before any commercial email goes out."""
    problems = []
    if not config.SENDER_EMAIL:
        problems.append("SENDER_EMAIL is empty -- recipients need a real reply-to address")
    if not config.SENDER_POSTAL_ADDRESS:
        problems.append("SENDER_POSTAL_ADDRESS is empty -- CAN-SPAM requires a physical postal address")
    if not config.UNSUBSCRIBE_LINE:
        problems.append("UNSUBSCRIBE_LINE is empty -- CAN-SPAM requires a working opt-out")
    return problems


def next_step_for(
    lead: Lead,
    history: List[Outreach],
    interval_days: int,
    max_followups: int,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Which message this lead is due for, or None if nothing is due.

    Returns 1 for a first contact, 2+ for follow-ups. A lead is due once
    `interval_days` have passed since the last message. The sequence stops
    as soon as they reply, opt out, or reach the follow-up cap.
    """
    now = now or datetime.utcnow()

    if lead.replied_at is not None or lead.opted_out:
        return None
    if (lead.status or "").lower() in TERMINAL_STATUSES:
        return None

    delivered = sorted(
        [row for row in history if row.delivery != "failed"],
        key=lambda row: row.created_at or datetime.min,
    )
    if not delivered:
        return 1

    if len(delivered) >= max_followups + 1:
        return None

    last = delivered[-1]
    last_at = last.created_at or datetime.min
    if now - last_at < timedelta(days=interval_days):
        return None

    return len(delivered) + 1


def plan_outreach(
    session,
    only_manufacturers: bool,
    limit: Optional[int],
    interval_days: int,
    max_followups: int,
    now: Optional[datetime] = None,
) -> List[Tuple[Lead, int]]:
    """(lead, step) pairs that are due for a message right now."""
    query = session.query(Lead).filter(Lead.email.isnot(None), Lead.email != "")
    if only_manufacturers:
        query = query.filter(Lead.manufactures.is_(True))

    due: List[Tuple[Lead, int]] = []
    for lead in query.order_by(Lead.created_at):
        step = next_step_for(lead, list(lead.outreach), interval_days, max_followups, now)
        if step is not None:
            due.append((lead, step))

    return due[:limit] if limit is not None else due


def build_email(lead: Lead, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.SENDER_NAME, config.SENDER_EMAIL))
    msg["To"] = lead.email
    if config.SENDER_EMAIL:
        msg["Reply-To"] = config.SENDER_EMAIL
    msg.set_content(body)
    return msg


def write_draft(msg: EmailMessage, lead: Lead, drafts_dir: Path, step: int = 1) -> Path:
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / f"{lead.id:04d}-step{step}-{lead.domain.replace('.', '_')}.eml"
    path.write_bytes(bytes(msg))
    return path


def send_via_smtp(msg: EmailMessage) -> None:
    if not config.SMTP_HOST:
        raise RuntimeError("SMTP_HOST is not set; cannot send")
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        if config.SMTP_USERNAME:
            smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        smtp.send_message(msg)


def run(
    message_file: Path,
    drafts_dir: Path,
    only_manufacturers: bool,
    limit: Optional[int],
    send: bool,
    dry_run: bool,
    followup_file: Path = DEFAULT_FOLLOWUP_FILE,
    interval_days: int = 3,
    max_followups: int = 4,
) -> dict:
    init_db()
    first_subject, first_body = load_message(message_file)
    follow_subject, follow_body = load_message(followup_file)
    rendered = {
        1: (first_subject, render(first_body)),
        2: (follow_subject, render(follow_body)),
    }

    problems = compliance_problems()
    if problems:
        for problem in problems:
            logger.warning("SETTINGS: %s", problem)
        if send:
            sys.exit("Refusing to send with the settings above unfilled. Fix them in .env first.")

    session = SessionLocal()
    stats = {"prepared": 0, "first_contact": 0, "followups": 0, "sent": 0, "drafted": 0, "failed": 0}

    try:
        due = plan_outreach(session, only_manufacturers, limit, interval_days, max_followups)
        contactable = session.query(Lead).filter(Lead.email.isnot(None), Lead.email != "").count()
        logger.info(
            "%d of %d contactable leads are due now (every %d days, %d follow-ups max)",
            len(due), contactable, interval_days, max_followups,
        )

        for lead, step in due:
            # Step 1 uses the intro; every later step uses the follow-up copy.
            subject, body = rendered[1] if step == 1 else rendered[2]
            label = "first" if step == 1 else f"follow-up #{step - 1}"

            if dry_run:
                logger.info("[dry-run] %-12s -> %s <%s>", label, lead.company_name, lead.email)
                stats["prepared"] += 1
                continue

            msg = build_email(lead, subject, body)

            if send:
                try:
                    send_via_smtp(msg)
                    delivery, error = "sent", None
                    stats["sent"] += 1
                    logger.info("sent   %-12s -> %s <%s>", label, lead.company_name, lead.email)
                except Exception as exc:  # keep going; record why this one failed
                    delivery, error = "failed", str(exc)
                    stats["failed"] += 1
                    logger.error("FAILED %-12s -> %s <%s>: %s", label, lead.company_name, lead.email, exc)
            else:
                path = write_draft(msg, lead, drafts_dir, step)
                delivery, error = "drafted", None
                stats["drafted"] += 1
                logger.info("draft  %-12s -> %s <%s>  (%s)", label, lead.company_name, lead.email, path.name)

            session.add(
                Outreach(
                    lead_id=lead.id,
                    to_email=lead.email,
                    step=step,
                    subject=subject,
                    body=body,
                    delivery=delivery,
                    error=error,
                )
            )
            if delivery != "failed":
                lead.status = "contacted"
                stats["first_contact" if step == 1 else "followups"] += 1
            session.commit()
            stats["prepared"] += 1

            if send:
                time.sleep(config.EMAIL_DELAY_SECONDS)
    finally:
        session.close()

    logger.info("Done. %s", stats)
    if stats["drafted"]:
        print(f"\n{stats['drafted']} drafts written to {drafts_dir}")
        print("Review them, then send with your mail client, or re-run with --send.")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--message-file", type=Path, default=DEFAULT_MESSAGE_FILE)
    parser.add_argument("--drafts-dir", type=Path, default=DEFAULT_DRAFTS_DIR)
    parser.add_argument("--only-manufacturers", action="store_true", help="Only leads that synthesize their own product")
    parser.add_argument("--followup-file", type=Path, default=DEFAULT_FOLLOWUP_FILE)
    parser.add_argument(
        "--interval-days", type=int, default=3,
        help="Days to wait before each follow-up (default 3)",
    )
    parser.add_argument(
        "--max-followups", type=int, default=4,
        help="Follow-ups after the first email before giving up (default 4)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap how many leads to contact this run")
    parser.add_argument("--send", action="store_true", help="Actually deliver over SMTP instead of writing drafts")
    parser.add_argument(
        "--i-understand-this-sends-real-email",
        action="store_true",
        help="Required alongside --send. Real messages to real companies cannot be recalled.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List who would be contacted; write nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.send and not args.i_understand_this_sends_real_email:
        sys.exit(
            "--send delivers real email to real companies and cannot be undone.\n"
            "Re-run with --send --i-understand-this-sends-real-email once you have\n"
            "reviewed the drafts in outreach_drafts/."
        )

    run(
        message_file=args.message_file,
        drafts_dir=args.drafts_dir,
        only_manufacturers=args.only_manufacturers,
        limit=args.limit,
        send=args.send,
        dry_run=args.dry_run,
        followup_file=args.followup_file,
        interval_days=args.interval_days,
        max_followups=args.max_followups,
    )


if __name__ == "__main__":
    main()
