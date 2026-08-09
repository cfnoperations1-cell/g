"""Outreach agent: emails peptide vendor leads the scraper agent found.

Reads leads from the shared CRM database, writes one message per lead, and
records it in the outreach table so nobody is ever contacted twice -- which
matters because the scraper keeps adding vendors and this runs behind it on
a schedule.

Two modes:
  drafts (default) -- writes .eml files to outreach_drafts/ for you to review
                      and send yourself. Nothing leaves the machine.
  --send           -- delivers via SMTP. Requires SMTP_* settings in .env and
                      an explicit --i-understand-this-sends-real-email flag.

Usage:
    python -m emailer.agent --dry-run              # show who would be contacted
    python -m emailer.agent                        # write drafts to review
    python -m emailer.agent --only-manufacturers   # narrow to labs that synthesize
    python -m emailer.agent --send --i-understand-this-sends-real-email
"""
from __future__ import annotations

import argparse
import logging
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import List, Optional, Tuple

import config
from db import SessionLocal, init_db
from models import Lead, Outreach

logger = logging.getLogger(__name__)

DEFAULT_MESSAGE_FILE = Path(__file__).parent / "message.txt"
DEFAULT_DRAFTS_DIR = Path(config.BASE_DIR) / "outreach_drafts"


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


def pending_leads(session, only_manufacturers: bool, limit: Optional[int]) -> List[Lead]:
    """Leads with an email address that we have never written to before."""
    contacted_ids = {row.lead_id for row in session.query(Outreach.lead_id).distinct()}

    query = session.query(Lead).filter(Lead.email.isnot(None), Lead.email != "")
    if only_manufacturers:
        query = query.filter(Lead.manufactures.is_(True))

    leads = [lead for lead in query.order_by(Lead.created_at) if lead.id not in contacted_ids]
    return leads[:limit] if limit is not None else leads


def build_email(lead: Lead, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.SENDER_NAME, config.SENDER_EMAIL))
    msg["To"] = lead.email
    if config.SENDER_EMAIL:
        msg["Reply-To"] = config.SENDER_EMAIL
    msg.set_content(body)
    return msg


def write_draft(msg: EmailMessage, lead: Lead, drafts_dir: Path) -> Path:
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / f"{lead.id:04d}-{lead.domain.replace('.', '_')}.eml"
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
) -> dict:
    init_db()
    subject, raw_body = load_message(message_file)
    body = render(raw_body)

    problems = compliance_problems()
    if problems:
        for problem in problems:
            logger.warning("SETTINGS: %s", problem)
        if send:
            sys.exit("Refusing to send with the settings above unfilled. Fix them in .env first.")

    session = SessionLocal()
    stats = {"prepared": 0, "sent": 0, "drafted": 0, "failed": 0, "skipped_already_contacted": 0}

    try:
        leads = pending_leads(session, only_manufacturers, limit)
        total_with_email = session.query(Lead).filter(Lead.email.isnot(None), Lead.email != "").count()
        stats["skipped_already_contacted"] = total_with_email - len(leads)

        logger.info(
            "%d leads to contact (%d already contacted, skipped)",
            len(leads), stats["skipped_already_contacted"],
        )

        for lead in leads:
            if dry_run:
                logger.info("[dry-run] would email %s <%s>", lead.company_name, lead.email)
                stats["prepared"] += 1
                continue

            msg = build_email(lead, subject, body)

            if send:
                try:
                    send_via_smtp(msg)
                    delivery, error = "sent", None
                    stats["sent"] += 1
                    logger.info("sent -> %s <%s>", lead.company_name, lead.email)
                except Exception as exc:  # keep going; record why this one failed
                    delivery, error = "failed", str(exc)
                    stats["failed"] += 1
                    logger.error("FAILED -> %s <%s>: %s", lead.company_name, lead.email, exc)
            else:
                path = write_draft(msg, lead, drafts_dir)
                delivery, error = "drafted", None
                stats["drafted"] += 1
                logger.info("draft  -> %s <%s>  (%s)", lead.company_name, lead.email, path.name)

            session.add(
                Outreach(
                    lead_id=lead.id,
                    to_email=lead.email,
                    subject=subject,
                    body=body,
                    delivery=delivery,
                    error=error,
                )
            )
            if delivery != "failed":
                lead.status = "contacted"
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
    )


if __name__ == "__main__":
    main()
