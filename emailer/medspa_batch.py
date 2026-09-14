"""Med spa / clinic outreach from the by-state export.

Picks the strongest leads from exports/medspa_peptides_with_email_<date>.csv, personalises
emailer/message_medspa.txt per lead (business name + the peptides their own site lists),
and writes .eml drafts to outreach_drafts/medspa/ for review. Sending is opt-in and uses
the same SMTP path, compliance checks and pacing as emailer/agent.py. Every contact is
logged to outreach_drafts/medspa/sent_log.csv so a business is never emailed twice.

    python -m emailer.medspa_batch --limit 30                 # drafts only
    python -m emailer.medspa_batch --limit 30 --message-file emailer/medspa/margin.txt
    python -m emailer.medspa_batch --limit 30 --send --i-understand-this-sends-real-email
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import time
from collections import defaultdict
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

import config
from emailer.agent import compliance_problems, load_message, render, send_via_smtp

ROOT = Path(config.BASE_DIR)
DRAFTS = ROOT / "outreach_drafts" / "medspa"
LOG = DRAFTS / "sent_log.csv"
MESSAGE = ROOT / "emailer" / "message_medspa.txt"

# how a crawl-detected term should read in the email
PRETTY = {
    "bpc-157": "BPC-157", "bpc157": "BPC-157", "tb-500": "TB-500", "tb500": "TB-500", "semaglutide": "semaglutide",
    "tirzepatide": "tirzepatide", "retatrutide": "retatrutide", "sermorelin": "sermorelin", "ipamorelin": "ipamorelin",
    "cjc-1295": "CJC-1295", "cjc1295": "CJC-1295", "tesamorelin": "tesamorelin", "nad+": "NAD+", "nad plus": "NAD+",
    "glp-1": "GLP-1s", "glp1": "GLP-1s", "pt-141": "PT-141", "pt141": "PT-141", "ghk-cu": "GHK-Cu", "ghk cu": "GHK-Cu",
    "mots-c": "MOTS-c", "motsc": "MOTS-c", "aod-9604": "AOD-9604", "aod9604": "AOD-9604", "epitalon": "epitalon",
    "thymosin": "thymosin", "kisspeptin": "kisspeptin", "selank": "selank", "semax": "semax", "dsip": "DSIP",
    "5-amino-1mq": "5-Amino-1MQ", "ss-31": "SS-31", "hexarelin": "hexarelin", "gonadorelin": "gonadorelin",
    "oxytocin": "oxytocin", "melanotan": "melanotan", "tesofensine": "tesofensine", "cagrilintide": "cagrilintide",
}
ORDER = ["semaglutide", "tirzepatide", "retatrutide", "bpc-157", "tb-500", "sermorelin", "ipamorelin", "cjc-1295",
         "tesamorelin", "nad+", "glp-1", "pt-141", "ghk-cu", "mots-c", "aod-9604"]
SKIP_LOCAL = ("privacy", "legal", "abuse", "webmaster", "postmaster", "noreply", "no-reply", "billing", "careers", "jobs", "press", "hr@", "corrections", "dmca", "security")
# a practice, not a peptide vendor / directory / trainer
PRACTICE = re.compile(r"spa|wellness|aesthetic|clinic|health|rejuv|vitality|medical|\bmd\b|\bdr\b|skin|laser|beauty|inject|weight|hormone|longevity|anti.?aging|iv\b|hydration|regen|integrative|functional|family|center|centre|institute|physician|np\b|nurse|derm", re.I)
NOT_PRACTICE = re.compile(r"\bfind\b|peptide ?(supply|store|shop|source|direct|finder|probe|clinic finder|guide|list|review|lab)|research|labs?\b|blend|wholesale|training|academy|course|finder|directory|reviews?|magazine|news|forum|reddit|shop\b|store\b|supply|vendor|catalog|coupon|discount", re.I)
EMAIL_HOST = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")
FREEMAIL = ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "protonmail.com")


def peptide_phrase(terms: str) -> str:
    found = [t.strip().lower() for t in terms.split(",") if t.strip()]
    names, seen = [], set()
    for key in ORDER + sorted(found):
        for t in found:
            if (t == key or t.replace("-", "") == key.replace("-", "")) and PRETTY.get(t, t) not in seen:
                names.append(PRETTY.get(t, t)); seen.add(PRETTY.get(t, t))
    names = [n for n in names if n.lower() not in ("peptide", "peptides")]
    if not names:
        return "peptide therapy"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]}, {names[1]} and {names[2]}"


GENERIC = {"home", "homepage", "welcome", "index", "main", "med spa", "medical spa", "medspa", "clinic", "services", "about", "contact"}
NOISE = re.compile(r"\b(best|top rated|near me|official site|official website|book now|online|llc|inc|pllc)\b", re.I)


def name_from_domain(dom: str) -> str:
    base = dom.split("/")[0].split(".")[0]
    base = re.sub(r"(medspa|medicalspa|wellness|aesthetics|clinic|health|md)$", lambda m: " " + m.group(1), base)
    words = re.sub(r"[-_]+", " ", base).split()
    return " ".join(w.capitalize() if w not in ("md",) else "MD" for w in words)


def clean_name(name: str, dom: str = "") -> str:
    n = re.split(r"\s*[|\-–—:•(\[]\s*", name, maxsplit=1)[0].strip()
    n = NOISE.sub("", n).strip(" ,.&")
    n = re.sub(r"\s{2,}", " ", n)
    titleish = bool(re.search(r",|&| in [A-Z]|\bnear\b|\bbest\b|\d", n)) or len(n.split()) > 5
    if not n or n.lower() in GENERIC or len(n) < 3 or len(n) > 42 or titleish:
        n = dom.split("/")[0] if dom else ""   # the bare domain reads naturally and is always right
    return n or "your practice"


def already_contacted() -> set:
    if not LOG.exists():
        return set()
    with open(LOG, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    sent = [r for r in rows if r["mode"] == "sent"]
    return {r["email"].lower() for r in sent} | {r["domain"] for r in sent}


def pick_leads(limit: int, source: Path) -> list:
    done = already_contacted()
    with open(source, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    cands = []
    for r in rows:
        email = r["email"].strip().lower()
        dom = r["website"].replace("https://", "").strip("/")
        if not email or "@" not in email or email in done or dom in done:
            continue
        if "site confirmed" not in r["category"]:
            continue
        local, host = email.split("@", 1)
        if any(local.startswith(s) or s in local for s in SKIP_LOCAL) or not EMAIL_HOST.match(host) or re.search(r"\.(avif|png|jpg|webp|gif|svg)$", host):
            continue
        blob = f"{r['business_name']} {dom}"
        if NOT_PRACTICE.search(blob) or not PRACTICE.search(blob):
            continue
        hits = int(r["peptide_hits"] or 0)
        if hits < 2:
            continue
        own_domain = host.endswith(dom.split("/")[0]) if dom else False
        score = hits + (5 if own_domain else 0) - (3 if host in FREEMAIL else 0) + (2 if local in ("info", "hello", "contact", "office", "frontdesk") else 0)
        cands.append((score, r, email, dom))
    cands.sort(key=lambda x: -x[0])
    # spread across states: round-robin over per-state queues sorted by score
    by_state = defaultdict(list)
    for c in cands:
        by_state[c[1]["state"] or "??"].append(c)
    picked, seen_dom = [], set()
    while len(picked) < limit and any(by_state.values()):
        for st in sorted(by_state, key=lambda s: -len(by_state[s])):
            while by_state[st]:
                c = by_state[st].pop(0)
                if c[3] in seen_dom:
                    continue
                picked.append(c); seen_dom.add(c[3]); break
            if len(picked) >= limit:
                break
    return picked


def build(lead_row: dict, email: str, subject_t: str, body_t: str) -> EmailMessage:
    name = clean_name(lead_row["business_name"], lead_row["website"].replace("https://", "").strip("/"))
    peps = peptide_phrase(lead_row["peptide_terms_found"])
    subject = subject_t.replace("{business_name}", name).replace("{peptides}", peps)
    body = render(body_t.replace("{business_name}", name).replace("{peptides}", peps))
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.SENDER_NAME, config.SENDER_EMAIL or "sender@example.com"))
    msg["To"] = email
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    return msg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--message-file", type=Path, default=MESSAGE, help="copy to use (emailer/message_medspa.txt or any file in emailer/medspa/)")
    ap.add_argument("--source", type=Path, default=None, help="med spa with-email CSV (default: newest in exports/)")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--i-understand-this-sends-real-email", action="store_true")
    args = ap.parse_args()
    source = args.source or Path(sorted(glob.glob(str(ROOT / "exports" / "medspa_peptides_with_email_*.csv")))[-1])
    subject_t, body_t = load_message(args.message_file)
    if args.send:
        if not args.i_understand_this_sends_real_email:
            raise SystemExit("--send requires --i-understand-this-sends-real-email after you have reviewed the drafts")
        problems = compliance_problems()
        if not config.SENDER_EMAIL:
            problems.append("SENDER_EMAIL is empty")
        if problems:
            raise SystemExit("Refusing to send:\n  - " + "\n  - ".join(problems))
    DRAFTS.mkdir(parents=True, exist_ok=True)
    picked = pick_leads(args.limit, source)
    new_log = not LOG.exists()
    with open(LOG, "a", newline="", encoding="utf-8") as logf:
        w = csv.writer(logf)
        if new_log:
            w.writerow(["sent_at", "mode", "business_name", "state", "domain", "email", "subject"])
        for i, (score, r, email, dom) in enumerate(picked, 1):
            msg = build(r, email, subject_t, body_t)
            path = DRAFTS / f"{i:02d}_{r['state'] or 'XX'}_{re.sub(r'[^a-z0-9]+', '-', dom.lower())[:40]}.eml"
            path.write_bytes(bytes(msg))
            mode = "draft"
            if args.send:
                send_via_smtp(msg); mode = "sent"
                time.sleep(config.EMAIL_DELAY_SECONDS)
            w.writerow([time.strftime("%Y-%m-%d %H:%M"), mode, clean_name(r["business_name"], dom), r["state"], dom, email, msg["Subject"]])
            print(f"{i:2d}. [{mode}] {r['state']:2s} {clean_name(r['business_name'], dom)[:34]:34s} <{email}>  ({peptide_phrase(r['peptide_terms_found'])})")
    print(f"\n{len(picked)} {'sent' if args.send else 'drafts written to ' + str(DRAFTS.relative_to(ROOT))}")


if __name__ == "__main__":
    main()
