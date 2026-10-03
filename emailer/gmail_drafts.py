"""Put generated .eml drafts into a Gmail Drafts folder over IMAP.

Works with a Google Workspace or Gmail mailbox that has IMAP enabled and an app password
(Google Account -> Security -> 2-Step Verification -> App passwords). Attachments travel
from disk inside the .eml, so nothing is retyped. Nothing is sent: they land as drafts.

    python -m emailer.gmail_drafts outreach_drafts/medspa            # all .eml in the folder
    python -m emailer.gmail_drafts outreach_drafts/medspa --limit 5  # first 5 only

.env needs:  SMTP_USERNAME=<mailbox>  SMTP_PASSWORD=<app password>
(the same two values let emailer.medspa_batch --send deliver through smtp.gmail.com:587)
"""
from __future__ import annotations

import argparse
import glob
import imaplib
import time
from pathlib import Path

import config

FOLDER = "[Gmail]/Drafts"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    user, pw = config.SMTP_USERNAME, config.SMTP_PASSWORD
    if not user or not pw:
        raise SystemExit("SMTP_USERNAME / SMTP_PASSWORD (app password) are not set in .env")
    files = sorted(glob.glob(str(args.folder / "*.eml")))[: args.limit]
    if not files:
        raise SystemExit(f"no .eml files in {args.folder}")
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    box.login(user, pw)
    ok = 0
    for f in files:
        raw = Path(f).read_bytes()
        typ, _ = box.append(FOLDER, r"(\Draft \Seen)", imaplib.Time2Internaldate(time.time()), raw)
        print(f"{'ok ' if typ == 'OK' else 'ERR'} {Path(f).name}")
        ok += typ == "OK"
        time.sleep(0.5)
    box.logout()
    print(f"\n{ok}/{len(files)} drafts placed in {FOLDER} for {user}")


if __name__ == "__main__":
    main()
