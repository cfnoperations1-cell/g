"""Standalone, dependency-free send engine for the outreach campaign.

Drives real sends (initial outreach + timed follow-ups) with durable tracking, using
ONLY the Python standard library so the hourly loop runs under plain `python3`
after any container/clone restart. All state lives in files committed to the repo.

    python3 outreach/serve_send.py stats
    python3 outreach/serve_send.py migrate                    # upgrade sent_log.csv schema in place
    python3 outreach/serve_send.py next 100 batch.json        # build the next wave (follow-ups due first,
                                                              #   then initial sends, vendors before med spas)
    python3 outreach/serve_send.py record batch.json [UPTO] [--skip 3,7]
                                                              # mark rows 1..UPTO of the batch as sent
    python3 outreach/serve_send.py mark replied|bounced|unsubscribed addrs.txt
                                                              # stop follow-ups for these emails/domains

State files (all under outreach/, all committed):
  draft_queue.csv  - every verified contact with its rendered initial subject/body (built by
                     emailer/draft_queue.py --build)
  sent_log.csv     - one row per contact we have emailed: when, how many follow-ups, status
  draft_ids.csv    - optional email -> Gmail draft id for contacts that already have a
                     verified draft; those are sent by draft id (which also clears the draft)

Follow-up cadence: FOLLOWUP_DAYS after the last touch, up to MAX_FOLLOWUPS per contact, and
never to anyone whose status is not "active" (replied / bounced / unsubscribed).
DAILY_CAP guards Google Workspace's daily sending limit; the wave size is clipped to it.
"""
import csv, html, json, os, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

csv.field_size_limit(10_000_000)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outreach"
QUEUE = OUT / "draft_queue.csv"
SENT = OUT / "sent_log.csv"
DRAFT_IDS = OUT / "draft_ids.csv"
_THIRD_PARTY = None
FU_TPL = {"medspa": ROOT / "emailer" / "followup_medspa.txt", "vendor": ROOT / "emailer" / "followup_vendor.txt"}

DAILY_CAP = int(os.environ.get("DAILY_CAP", "100"))      # ramp: 100 per day (Gmail throttled at ~220)
HOURLY_CAP = int(os.environ.get("HOURLY_CAP", "10"))     # ramp: 10 per hourly wave
FOLLOWUP_DAYS = int(os.environ.get("FOLLOWUP_DAYS", "3"))
MAX_FOLLOWUPS = int(os.environ.get("MAX_FOLLOWUPS", "3"))
FU_SHARE = float(os.environ.get("FU_SHARE", "0.5"))       # at most this fraction of a wave goes to follow-ups
FOLLOWUP_START = os.environ.get("FOLLOWUP_START", "2026-09-17T18:30:00Z")  # no follow-ups at all before this
PRIORITY_DOMAINS = ["heritagelabsusa.com"]                 # "peptide veterans": the one veteran-owned vendor
# vendors that are obviously not US-based get skipped (the pitch is US-made supply, no customs risk)
FOREIGN = re.compile(r"\.(ca|uk|co\.uk|is|cn|ae|eu|au|de|fr|in|mx|nl|ru|pl|es|it|br|hk|sg|nz|ie|ch|se|no|dk|fi|tw|jp|kr)$"
                     r"|costarica|french|german|british|canad|austral|europe|\buae\b|canada|europe|-uk\b|\buk-|uk\.(com|net|org)$", re.I)
# scraped page titles that are not a business name -> fall back to the bare domain
JUNK_VENDOR = re.compile(r"click here|\bpromo\b|\beligible\b|\beditor\b|\bnotes\b|\balternative\b|^visit\b|\bdosing\b|cheapest|^wholesale peptides$|marcus hansen|view source|^source$|^usa$|^recovery$|^peptides?$|^buy\b|for sale|coupon|discount|use code|^code\b|save \d|\d+% off|free shipping"
                         r"|\boffers?\b|wholesale medical|nasal spray|research peptides|→|↗|adipotide|glutathione|^ghrp"
                         r"|^pt$|^best\b|^top\b|\bshop$|\bstore$|^home$|^welcome$|^peptide$|^wholesale$|affiliate"
                         r"|^(high|low|new|free|fast|quality|premium|official|trusted|reliable|verified|tested|pure|safe"
                         r"|secure|online|orders?|products?|research|labs?|login|account|cart|menu|search|sales?|deals?|prices?)$", re.I)


FOREIGN_LOCAL = {"contato", "kontakt", "contacto", "info-de", "info-uk"}
FOREIGN_NAME = re.compile(r"\b(uae|dubai|uk|london|centre|wuhan|shanghai|shenzhen|beijing|hangzhou|guangzhou|nanjing"
                          r"|jinan|qingdao|tianjin|chengdu|xi'?an|suzhou|ningbo|zhengzhou|changsha|hefei|kunming|dalian"
                          r"|shijiazhuang|shandong|jiangsu|zhejiang|hubei|hunan|henan|hebei|anhui|sichuan|guangdong"
                          r"|hong kong|gmbh|s\.?r\.?l|b\.?v\.?|pty|sdn bhd|sdn\. bhd|ltd|limited|co\.,? ?ltd|trading co"
                          r"|canada|europe|costa rica|australia|india|china)\b", re.I)
# Place names run together inside a domain, where word boundaries never match:
# shandongyixinpeptides.com is Shandong province. Only tokens long and distinctive
# enough to be safe as substrings belong here -- "india" is left out because it sits
# inside "indiana", and "uk"/"eu" are far too short to risk.
FOREIGN_IN_DOMAIN = re.compile(
    r"shandong|jiangsu|zhejiang|guangdong|sichuan|shaanxi|liaoning|fujian|jiangxi|guizhou"
    r"|wuhan|shanghai|shenzhen|beijing|hangzhou|guangzhou|nanjing|jinan|qingdao|tianjin"
    r"|chengdu|suzhou|ningbo|zhengzhou|changsha|kunming|dalian|shijiazhuang|xiamen"
    r"|hongkong|chinese|gmbh|\bsarl\b", re.I)
# Country codes too short to use as substrings anywhere in a domain, but safe at the
# front of one: uaepeptideresearch.com is Dubai, while youngeryouaesthetics.com is a
# US med spa whose name simply runs "yoU AEsthetics" together. \buae\b in FOREIGN
# cannot help -- a run-together domain never offers the word boundary.
FOREIGN_DOMAIN_PREFIX = re.compile(r"^(uae|ksa|qatar|dubai|abudhabi)[a-z0-9]", re.I)

# Vendors confirmed non-US by looking at the site itself, where the stored name gives
# nothing away. Jinan Boruimei Trading Co., Ltd. is a Chinese trading company: the
# US-made, no-customs pitch has nothing to say to it.
FOREIGN_DOMAINS = {"boruimei.com",      # Jinan Boruimei Trading Co., Ltd.
                   "hnhkpeptide.com",   # "Hongke Biotechnology", a Chinese supplier
                   "peakpeptide.com",   # its own site says "EU Supplier"
                   "spresearchcenter.com",  # contact number on the page is +86 (China)
                   "peptuvia.com",      # marketplace shipping from China warehouses,
                                        # its front page schedules around Chinese holidays
                   "myotrope.com",      # its verified resellers are listed as Netherlands / Europe
                   "24hourpeptides.com",  # prices in GBP, next-day UK shipping, UK company number
                   "uwa-biotech.com",   # WhatsApp contact number is +86 (China)
                   "modernaminos.com",  # the only phone it publishes is +1 437, Toronto
                   "healtlab.com",      # every contact on its page is a +852 (Hong Kong)
                                        # WhatsApp or Telegram, against three gmail addresses
                   "yansenpeptidesfactory.com",  # "a leading peptide raw material manufacturer
                                        # based in China", with a Shenzhen street address
                   "sciencepeptidelab.com",  # "Direct factory supply. No trading intermediaries."
                                        # against two +852 (HK) WhatsApp numbers
                   "walkerchemicals.store",  # "Free delivery on orders over 250 pounds"
                   "homopeptide.co",    # "Ships from China warehouse ... 7-15 business days"
                   "hkroids.com",       # its contact page lists a +86 China number and four
                                        # +852 Hong Kong ones, against @hkroids.net addresses
                   "glunovabio.com",    # flies a US flag and trades as Prost Biotech; its About page
                                        # says PROST BIOTECH SDN BHD, "Malaysian Registered Business",
                                        # Bandar Bukit Jalil, Kuala Lumpur, and its only real number is
                                        # +65 (Singapore). The "+1 (628) 555-0193" it gives for its
                                        # account manager is in the 555-01xx range reserved for fiction.
                   "chapeptides.com"}   # trades as "CH Peptides Co., Ltd", but its own About page
                                        # says CHA MEDICAL TECHNOLOGY (Guangzhou) CO., LTD, with
                                        # "peptide synthesis capabilities in Guangzhou, China"


# Vendors we decline to approach for reasons that have nothing to do with where
# they are. aminoasylumofficial.com presents itself as the authorized successor to
# Amino Asylum, whose original operation was closed by FDA action in 2025, and at
# least eight lookalike domains trade on that name. Nothing on the page proves
# which one is the real business, and a wholesale pitch sent to the wrong one
# lands in a stranger's inbox under Jonathan's name.
#
# The two peptide "catalog" sites are not suppliers or buyers: thepeptidecatalog.com
# is a price-comparison directory ("Learn Peptides. Get the Best Price.") and
# peptidedosages.com publishes dosing charts. Both list the peptides we make, which
# is why the crawler scored them highly, but neither buys wholesale -- a domestic
# supply pitch is the wrong message and spends a send on a reader, not a customer.
# They could be worth approaching about being listed; that is a different email,
# which Jonathan has not written.
DECLINED_DOMAINS = {"aminoasylumofficial.com",
                    "thepeptidecatalog.com",
                    "peptidedosages.com",
                    "peptidelibrary.app"}   # "Compare Peptides, Track Research" -- a reference app

# Both domain sets above are matched against the domain of the address we would
# write to, and for a business that publishes a Gmail or Outlook address that is
# "gmail.com" -- so a business excluded for any reason, geography included, slips
# past them when it is only reachable at freemail. These are the exact addresses.
DECLINED_CONTACTS = {"thepeptidecatalog@gmail.com",  # price-comparison directory, not a supplier
                     "sec9vzion@outlook.com",        # peptidedosages.com, a dosing-chart site
                     "beatyjin51@gmail.com",         # healtlab.com, contactable only on +852 Hong Kong
                     "wyi556911@gmail.com",         # yansenpeptidesfactory.com, Shenzhen, China
                     "peptpedia@gmail.com",         # peptpedia.org, a peptide encyclopedia
                     "productsmax16@gmail.com"}     # arizona-mall.com: an American-sounding domain
                                                    # over "GMP Factory OEM Supply ... for Global Labs
                                                    # and Manufacturers" -- upstream of us, not a buyer


FREEMAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "proton.me", "protonmail.com", "pm.me", "tuta.com",
            "tutanota.com", "qq.com", "163.com", "icloud.com", "aol.com", "sudomail.com", "live.com", "msn.com"}
GENERIC_NAMES = {"your practice", "your business"}


# Bases too generic to identify a business. peptide.partners and peptides.com are
# different companies, so collapsing both to "peptide" would silence one of them.
GENERIC_BRANDS = {"peptide", "peptides", "lab", "labs", "bio", "research", "gmail", "shop", "store"}


def brand_key(domain):
    """Collapse a domain to the brand behind it, or "" when that cannot be told.

    A scraped queue carries one business under several hosts: arizonapeptides.us
    and arizonapeptidesus.com are one operator, as are ms-peptides.com and
    mspeptides.com. Emailing both is emailing one business twice. Drop the TLD,
    the punctuation and a trailing "us"/"usa" that only marks the country.
    """
    base = (domain or "").lower().split(":")[0]
    if base.startswith("www."):
        base = base[4:]
    base = re.sub(r"[^a-z0-9]", "", base.split(".")[0])
    base = re.sub(r"(usa|us)$", "", base)
    if len(base) < 6 or base in GENERIC_BRANDS:
        return ""
    return base


def contact_key(email, domain=""):
    """What counts as "the same business" when deduplicating contacts.

    One contact per domain is right for a company mailbox, but freemail is not a
    company. Three clinics that publish a Gmail address are three businesses, and
    keying them all on gmail.com meant the first one emailed locked out every
    later one for good -- silently, because they simply stopped appearing in the
    queue. Freemail contacts are therefore keyed on the address itself.
    """
    e = (email or "").strip().lower()
    d = (domain or "").strip().lower() or (e.split("@", 1)[1] if "@" in e else "")
    if d in FREEMAIL:
        return "addr:" + e
    return d


def is_foreign(domain, name=""):
    d = (domain or "").lower()
    # str.lstrip takes a SET of characters, not a prefix: "walkerchemicals.store"
    # .lstrip("www.") is "alkerchemicals.store", so every domain starting with w
    # missed this list entirely.
    bare = d[4:] if d.startswith("www.") else d
    if bare in FOREIGN_DOMAINS:
        return True
    if FOREIGN_IN_DOMAIN.search(d) or FOREIGN_DOMAIN_PREFIX.match(d):
        return True
    return bool(FOREIGN.search(domain) or FOREIGN_NAME.search(name or ""))


# Words that say nothing about WHICH business this is, so they cannot vouch for a
# scraped name on their own.
NAME_STOPWORDS = {"the", "and", "for", "peptide", "peptides", "research", "lab", "labs",
                  "bio", "biotech", "biotechnology", "inc", "llc", "co", "company",
                  "group", "usa", "shop", "store", "online", "buy", "best", "premium"}


def name_matches_domain(name, domain):
    """Does this scraped name plausibly belong to this domain?

    Scrapers pick up whatever a page happens to say, so a title can name a
    different business entirely -- warehousepeptides.com came back as "The Peptide
    Lab". Telling somebody "I came across The Peptide Lab" when they are not that
    company reads worse than naming their domain, so a name is only trusted when
    some distinctive word in it also appears in the domain.
    """
    d = (domain or "").lower()
    words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower())
             if len(w) >= 4 and w not in NAME_STOPWORDS]
    if not words:
        return False
    return any(w in d for w in words)


# Words that carry no identity on their own. A name made only of these is a page
# title, not a business: "BULK Supply" is the first segment of "BULK Supply - AOD
# 9604 5mg - Regenerate Peptides", and "New" is what is left of "New-U".
GENERIC_WORDS = set("""
    high low new free fast bulk quality premium official trusted reliable verified
    tested pure safe secure online order orders product products research lab labs
    login account cart menu search sale sales deal deals price prices pricing supply
    supplies shop store home welcome peptide peptides best top usa us buy wholesale
    the a an and of for your our inc llc co company group
""".split())


def usable_name(n):
    """Is this something we can put in front of a stranger in a subject line?

    A freemail host is not a business name; neither is a stub like "New" left over
    from a truncated page title, nor a phrase whose every word is generic.
    """
    n = (n or "").strip()
    if not n or len(n) < 4 or n.lower() in FREEMAIL:
        return False
    words = [w for w in re.split(r"[^A-Za-z0-9']+", n.lower()) if w]
    return bool(words) and not all(w in GENERIC_WORDS for w in words)


def clean_vendor(name, domain):
    # Names come out of page titles, so they arrive HTML-escaped: "Charleston
    # Men&#x27;s Clinic", "Vital Force Therapy &amp; Wellness". Unescaped, that
    # is what the recipient reads in the subject line of a cold email.
    n = html.unescape(name or "").strip()
    if not n or len(n) < 3 or len(n.split()) > 5 or JUNK_VENDOR.search(n):
        return domain
    if not name_matches_domain(n, domain):
        return domain
    # A scraped "name" that is itself a hostname is no better than the domain we
    # are writing to, and when the two disagree (regentide.net stored against
    # contact@regentide.com) the mismatch is visible in the subject line.
    if " " not in n and "." in n and n.lower().rstrip("/") != (domain or "").lower():
        return domain
    return n
LEGACY_SENT_AT = "2026-09-14T16:00:00Z"                    # all pre-schema sends went out on 2026-09-14

COLS = ["email", "domain", "audience", "mode", "via", "sent_at", "last_touch_at", "stage", "status"]
STAGE_LINES = {
    1: "Just floating this back to the top of your inbox.",
    2: "Checking in once more in case this got buried.",
    3: "Last note from me on this -- I don't want to clutter your inbox.",
}
DEFAULT_SENDER = {
    "SENDER_NAME": "Jonathan Cole",
    "SENDER_EMAIL": "jonathan@marinexisbiologics.com",
    "SENDER_COMPANY": "Marinexis Biologics",
    "SENDER_POSTAL_ADDRESS": "6671 S Las Vegas Blvd, Las Vegas, NV 89118",
}
UNSUB_LINE = "Don't want to hear from us again? Reply with \"unsubscribe\" and we'll remove you."


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sender():
    d = dict(DEFAULT_SENDER)
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in d and v:
                    d[k] = v
    return d


# ---------- state ----------
def load_sent():
    rows = []
    if SENT.exists():
        with open(SENT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({k: (r.get(k) or "").strip() for k in COLS})
    for r in rows:                                   # tolerate the pre-follow-up schema
        r["email"] = r["email"].lower()
        r["domain"] = r["domain"] or r["email"].split("@", 1)[1]
        r["mode"] = r["mode"] or "sent"
        r["sent_at"] = r["sent_at"] or LEGACY_SENT_AT
        r["last_touch_at"] = r["last_touch_at"] or r["sent_at"]
        r["stage"] = r["stage"] or "0"
        r["status"] = r["status"] or "active"
    return rows


def save_sent(rows):
    with open(SENT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader(); w.writerows(rows)


def load_queue():
    with open(QUEUE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_draft_ids():
    m = {}
    if DRAFT_IDS.exists():
        with open(DRAFT_IDS, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                m[r["email"].strip().lower()] = r["draft_id"].strip()
    return m


def local_day(ts_iso):
    """Calendar day in Pacific time (Jonathan's day), so the 100/day cap resets at local midnight, not UTC."""
    from zoneinfo import ZoneInfo
    return parse(ts_iso).astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def today_count(rows):
    t = local_day(iso(now()))
    return sum(1 for r in rows if local_day(r["last_touch_at"]) == t)


# ---------- rendering ----------
def peps_of(qrow):
    if qrow and qrow.get("audience") == "medspa":
        m = re.search(r"and saw you offer (.*?) -- that's exactly", qrow["body"], re.S)
        if m:
            return m.group(1).replace("\n", " ")
    return "peptides"


def orig_subject(aud, name):
    tail = "US-made peptide supply for your practice" if aud == "medspa" else "US-made peptide supply, wholesale"
    return tail if name in GENERIC_NAMES else f"{name} - {tail}"


def render_fu(aud, name, peps, stage, sd):
    text = FU_TPL[aud].read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    subject_t = first.split(":", 1)[1].strip() if first.lower().startswith("subject:") else "Re: {orig_subject}"
    body_t = rest.lstrip("\n").rstrip("\n")
    subs = {
        "{orig_subject}": orig_subject(aud, name), "{business_name}": name, "{peptides}": peps,
        "{stage_line}": STAGE_LINES.get(stage, STAGE_LINES[max(STAGE_LINES)]),
        "{sender_email}": sd["SENDER_EMAIL"], "{sender_company}": sd["SENDER_COMPANY"],
        "{sender_postal_address}": sd["SENDER_POSTAL_ADDRESS"], "{unsubscribe_line}": UNSUB_LINE,
        "{sender_name}": sd["SENDER_NAME"],
    }
    for k, v in subs.items():
        subject_t = subject_t.replace(k, v); body_t = body_t.replace(k, v)
    return subject_t, body_t


# ---------- selection ----------
def third_party():
    """Addresses that belong to a different business than the site they came from.

    The crawler records every address on a vendor's pages, and some are somebody
    else's: lgipeptides.com publishes its marketing agency's, scientificamerican.com
    its publisher's, and a few sites publish placeholders like jane.smith@clinic.com.
    Built by outreach/audit_third_party.py; see that file for how the call is made.
    """
    global _THIRD_PARTY
    if _THIRD_PARTY is None:
        path = OUT / "third_party_contacts.csv"
        if not path.exists():
            _THIRD_PARTY = set()
        else:
            with open(path, newline="", encoding="utf-8") as f:
                _THIRD_PARTY = {r["email"].strip().lower() for r in csv.DictReader(f) if r.get("email")}
    return _THIRD_PARTY


def initial_candidates(sent_rows):
    done = set()
    for r in sent_rows:
        done.add(r["email"]); done.add(contact_key(r["email"], r["domain"]))
    cands = []
    for r in load_queue():
        e = r["email"].strip().lower()
        d = e.split("@", 1)[1]
        if e in done or contact_key(e) in done:
            continue
        if d in DECLINED_DOMAINS or e in DECLINED_CONTACTS or e in third_party():
            continue
        if r["audience"] == "vendor" and is_foreign(d, r.get("business_name", "")):
            continue
        if e.split("@", 1)[0] in FOREIGN_LOCAL:                       # e.g. contato@ (Portuguese), kontakt@ (German)
            continue
        cands.append(r)
    dids = load_draft_ids()                                       # file order = top of the Drafts folder first
    by_email = {r["email"].strip().lower(): r for r in cands}
    drafted = [by_email[e] for e in dids if e in by_email]
    rest = [r for r in cands if r["email"].strip().lower() not in dids]
    vendors = [r for r in rest if r["audience"] == "vendor"]
    medspas = [r for r in rest if r["audience"] == "medspa"]
    ordered = vendors + medspas                                   # after the drafts: vendors first, then med spas
    pri = [r for r in ordered if r["email"].split("@", 1)[1] in PRIORITY_DOMAINS]
    ordered = drafted + pri + [r for r in ordered if r not in pri]
    out, seen, brands = [], set(), {brand_key(r["domain"]) for r in sent_rows} - {""}
    for r in ordered:                                             # one contact per domain per campaign
        d = contact_key(r["email"])
        if d in seen:
            continue
        b = brand_key(d)
        if b and b in brands:                                     # ...and one per brand behind the domain
            continue
        seen.add(d)
        if b:
            brands.add(b)
        out.append(r)
    return out


def skip_contact(email, audience="", domain=""):
    """Should this contact be left alone, whatever stage it is at?"""
    d = (domain or (email.split("@", 1)[1] if "@" in email else "")).lower()
    if d in DECLINED_DOMAINS or email in DECLINED_CONTACTS or email in third_party():
        return True
    if audience == "vendor" and is_foreign(d):
        return True
    return email.split("@", 1)[0] in FOREIGN_LOCAL


def due_followups(sent_rows):
    if now() < parse(FOLLOWUP_START):
        return []
    cutoff = now() - timedelta(days=FOLLOWUP_DAYS)
    # The same gates that decide who we start writing to decide who we keep
    # writing to. A vendor only shown to be foreign after its first email --
    # hkroids.com publishes a +86 number and four +852 ones -- was still queued
    # for follow-ups, because the filters only ran over new candidates.
    due = [r for r in sent_rows
           if r["status"] == "active" and int(r["stage"]) < MAX_FOLLOWUPS and parse(r["last_touch_at"]) <= cutoff
           and not skip_contact(r["email"].strip().lower(), r.get("audience", ""), r.get("domain", ""))]
    due.sort(key=lambda r: r["last_touch_at"])
    return due


# ---------- commands ----------
def cmd_next(n, out_json):
    rows = load_sent(); qi = {r["email"].strip().lower(): r for r in load_queue()}
    dids = load_draft_ids(); sd = sender()
    budget = max(0, DAILY_CAP - today_count(rows)); n = min(n, budget, HOURLY_CAP)
    items = []
    fu_cap = int(n * FU_SHARE + 0.999) if n else 0
    for r in due_followups(rows):
        if len(items) >= fu_cap:
            break
        qrow = qi.get(r["email"]); aud = r["audience"] if r["audience"] in FU_TPL else "vendor"
        name = (qrow or {}).get("business_name") or r["domain"]; peps = peps_of(qrow); stage = int(r["stage"]) + 1
        if aud == "vendor":
            name = clean_vendor(name, r["domain"])
        if name == r["domain"] and r["domain"] in FREEMAIL:      # a freemail domain is not a business name
            name = "your practice" if aud == "medspa" else "your business"
        subj, body = render_fu(aud, name, peps, stage, sd)
        items.append({"kind": "fu", "stage": stage, "audience": aud, "to": r["email"], "name": name,
                      "peps": peps if aud == "medspa" else "-", "subject": subj, "body": body, "draftId": ""})
    for r in initial_candidates(rows):
        if len(items) >= n:
            break
        e = r["email"].strip().lower()
        name, subj, body, did = r["business_name"], r["subject"], r["body"], dids.get(e, "")
        em_dom = e.split("@", 1)[1]
        if r["audience"] == "vendor":
            # clean_vendor checks the name against the domain we are writing to.
            # For a contact who publishes a Gmail or Outlook address that check is
            # meaningless -- the name will never match "gmail.com" -- and the
            # fallback made the subject line read "gmail.com - US-made peptide
            # supply, wholesale". The queued name was already checked against the
            # business's own site at ingest, so for freemail it stands as it is.
            new = name if em_dom in FREEMAIL else clean_vendor(name, em_dom)
            if not usable_name(new):
                new = "your business"
            if new != name:
                subj = orig_subject("vendor", new)
                body = body.replace(f"I came across {name}\n", f"I came across {new}\n", 1)
                name, did = new, ""                              # never send a draft whose name we rewrote
        items.append({"kind": "initial", "stage": 0, "audience": r["audience"], "to": e, "name": name,
                      "peps": peps_of(r) if r["audience"] == "medspa" else "-", "subject": subj,
                      "body": body, "draftId": did})
    Path(out_json).write_text(json.dumps(items), encoding="utf-8")
    fu = sum(1 for i in items if i["kind"] == "fu"); ini = len(items) - fu
    print(f"BATCH {len(items)}  fu={fu} initial={ini}  (budget_left_today={budget}, cap={DAILY_CAP})")
    bad = [i["to"] for i in items if "{" in i["subject"] or "{" in i["body"]]
    if bad:
        print("!!! PLACEHOLDER LEFTOVERS:", bad)
    for i, it in enumerate(items, 1):
        kind = f"FU{it['stage']}" if it["kind"] == "fu" else "INIT"
        aud = "MS" if it["audience"] == "medspa" else "VN"
        print(f"{i}\t{kind}\t{aud}\t{it['to']}\t{it['name']}\t{it['peps']}\t{it['draftId'] or '-'}")


def cmd_record(batch_json, upto=None, skip=()):
    items = json.loads(Path(batch_json).read_text(encoding="utf-8"))
    rows = load_sent(); idx = {r["email"]: r for r in rows}; t = iso(now()); via = sender()["SENDER_EMAIL"]
    n_new = n_fu = 0
    for i, it in enumerate(items, 1):
        if upto and i > upto:
            break
        if i in skip:
            continue
        e = it["to"].strip().lower()
        if it["kind"] == "initial":
            if e in idx:
                continue                                          # idempotent
            row = {"email": e, "domain": e.split("@", 1)[1], "audience": it["audience"], "mode": "sent", "via": via,
                   "sent_at": t, "last_touch_at": t, "stage": "0", "status": "active"}
            rows.append(row); idx[e] = row; n_new += 1
        else:
            r = idx.get(e)
            if r and int(r["stage"]) < int(it["stage"]):
                r["stage"] = str(it["stage"]); r["last_touch_at"] = t; n_fu += 1
    save_sent(rows)
    print(f"recorded initial={n_new} followups={n_fu}  (rows 1..{upto or len(items)}, skipped {sorted(skip) or 'none'})")


def cmd_mark(status, path):
    assert status in ("replied", "bounced", "unsubscribed", "active"), status
    keys = {k.strip().lower().lstrip("<").rstrip(">") for k in Path(path).read_text(encoding="utf-8").split() if k.strip()}
    rows = load_sent(); n = 0
    for r in rows:
        if (r["email"] in keys or r["domain"] in keys) and r["status"] != status:
            r["status"] = status; n += 1
    save_sent(rows)
    print(f"marked {n} as {status}")


def cmd_export():
    """Write category lists: contacted (everyone emailed), replied, bounced, and the not-yet-emailed queue."""
    rows = load_sent()
    def dump(name, sel, cols):
        with open(OUT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
            for r in sel: w.writerow({k: r.get(k, "") for k in cols})
        return len(sel)
    base = ["email", "domain", "audience", "sent_at", "last_touch_at", "stage", "status"]
    n1 = dump("contacted.csv", [r for r in rows if r["status"] in ("active", "manual")], base)
    n2 = dump("replied.csv", [r for r in rows if r["status"] == "replied"], base)
    n3 = dump("bounced.csv", [r for r in rows if r["status"] == "bounced"], base)
    pend = initial_candidates(rows)
    n4 = dump("not_yet_emailed.csv", [{"email": r["email"], "domain": r["email"].split("@", 1)[1], "audience": r["audience"],
                                        "business_name": r["business_name"]} for r in pend],
              ["email", "domain", "audience", "business_name"])
    print(f"exported contacted={n1} replied={n2} bounced={n3} not_yet_emailed={n4}")


def cmd_stats():
    rows = load_sent()
    from collections import Counter
    st = Counter(r["status"] for r in rows)
    sg = Counter(r["stage"] for r in rows if r["status"] == "active")
    pend = len(initial_candidates(rows)); due = len(due_followups(rows)); tc = today_count(rows)
    active = [r for r in rows if r["status"] == "active" and int(r["stage"]) < MAX_FOLLOWUPS]
    nxt = min((parse(r["last_touch_at"]) + timedelta(days=FOLLOWUP_DAYS) for r in active), default=None)
    print(f"sent_total={len(rows)} status={dict(st)} active_by_stage={dict(sorted(sg.items()))}")
    print(f"today_sent={tc} budget_left_today={max(0, DAILY_CAP - tc)} cap={DAILY_CAP}")
    print(f"initial_pending={pend} followups_due_now={due} next_followup_due={iso(nxt) if nxt else '-'}")
    print("campaign=" + ("complete" if (pend == 0 and not active) else "running"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "stats":
        cmd_stats()
    elif a[0] == "migrate":
        save_sent(load_sent()); print("migrated", SENT)
    elif a[0] == "next":
        cmd_next(int(a[1]) if len(a) > 1 else 100, a[2] if len(a) > 2 else str(OUT.parent / ".send_batch.json"))
    elif a[0] == "record":
        upto = None; skip = set(); rest = a[2:]
        i = 0
        while i < len(rest):
            if rest[i] == "--skip":
                skip = {int(x) for x in rest[i + 1].split(",") if x.strip()}; i += 2
            else:
                upto = int(rest[i]); i += 1
        cmd_record(a[1], upto, skip)
    elif a[0] == "mark":
        cmd_mark(a[1], a[2])
    elif a[0] == "export":
        cmd_export()
    else:
        sys.exit("usage: serve_send.py [stats | migrate | next N out.json | record batch.json [UPTO] [--skip i,j] | mark STATUS file]")
