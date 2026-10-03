"""Visit each candidate med spa site and pull out what we need to email them.

Standard library only. For every domain in medspa/candidates.csv this fetches the
home page plus the likeliest contact pages, then records:

  - a contact email address (a real inbox, not a tracking or image address)
  - which peptides the practice actually offers (quoted back in the email)
  - a US signal (state/zip/phone on the page), since the pitch is US-made supply
  - the business name from the page title

    python3 medspa/enrich.py            # enrich every candidate not done yet
    python3 medspa/enrich.py 25         # only the next 25 (keeps runs short)
    python3 medspa/enrich.py --recheck  # re-crawl everything, including done rows

Results land in medspa/enriched.csv. Sites that block crawlers or publish no
address are recorded with status "no_email" and simply never reach the queue.
"""
import csv, re, socket, ssl, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "medspa"
CAND = HERE / "candidates.csv"
ENR = HERE / "enriched.csv"
TERMS_FILE = HERE / "peptide_terms.txt"
COLS = ["domain", "business_name", "email", "all_emails", "peptides", "peptide_hits",
        "us_signal", "city", "state", "phone", "url", "status", "checked_at"]

UA = "Mozilla/5.0 (compatible; MarinexisLeadBot/1.0; +https://marinexisbiologics.com)"
TIMEOUT = 15
CONTACT_PATHS = ["", "/contact", "/contact-us", "/contact.html", "/about", "/about-us",
                 "/services", "/peptides", "/peptide-therapy", "/book", "/appointments"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# addresses that exist on pages but are never a person: assets, vendors, examples
BAD_EMAIL = re.compile(r"(^|@)(no-?reply|donotreply|postmaster|abuse|webmaster|example|sentry|wixpress|squarespace"
                       r"|godaddy|shopify|cloudflare|sentry\.io|domain|privacy|dmca)", re.I)
BAD_EMAIL_END = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|woff2?)$", re.I)
PHONE_RE = re.compile(r"\(?\b([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})\b")
STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
          "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
# Anchor the state to a ZIP code. A bare ", MD" or ", PA" is far more often a
# credential after a clinician's name than it is Maryland or Pennsylvania.
STATE_ZIP_RE = re.compile(r",?\s*\b(" + "|".join(STATES) + r")\b[\s,]+(\d{5})(?:-\d{4})?\b")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)


def terms():
    if TERMS_FILE.exists():
        return [t.strip().lower() for t in TERMS_FILE.read_text(encoding="utf-8").splitlines()
                if t.strip() and not t.startswith("#")]
    return ["semaglutide", "tirzepatide", "peptide"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # many small practice sites have broken chains
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        raw = r.read(600_000)
    return raw.decode("utf-8", "replace")


def text_of(html):
    return re.sub(r"<[^>]+>", " ", TAG_RE.sub(" ", html))


def pick_email(found, domain):
    """Prefer an address on the practice's own domain, then a real-looking freemail one."""
    clean = []
    for e in found:
        e = e.strip().strip(".").lower()
        if BAD_EMAIL.search(e) or BAD_EMAIL_END.search(e) or len(e) > 70:
            continue
        clean.append(e)
    if not clean:
        return "", []
    seen, uniq = set(), []
    for e in clean:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    base = domain.split(".")[0]
    on_domain = [e for e in uniq if e.split("@", 1)[1] == domain or base in e.split("@", 1)[1]]
    pool = on_domain or uniq
    # a shared inbox beats a personal one for cold outreach
    for pref in ("info@", "hello@", "contact@", "office@", "frontdesk@", "admin@", "hi@", "team@", "support@"):
        for e in pool:
            if e.startswith(pref):
                return e, uniq
    return pool[0], uniq


def enrich_one(domain, seed_url=""):
    row = {c: "" for c in COLS}
    row["domain"] = domain
    row["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row["url"] = seed_url or f"https://{domain}/"
    base = f"https://{domain}"
    emails, blob, title, ok = [], "", "", False
    for path in CONTACT_PATHS:
        try:
            html = fetch(base + path)
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, ssl.SSLError, ValueError, OSError):
            continue
        except Exception:
            continue
        ok = True
        if not title:
            m = TITLE_RE.search(html)
            if m:
                title = re.sub(r"\s+", " ", text_of(m.group(1))).strip()[:120]
        emails += EMAIL_RE.findall(html)
        blob += " " + text_of(html).lower()
        if emails and len(blob) > 20_000:
            break
    if not ok:
        row["status"] = "unreachable"
        return row

    row["business_name"] = title
    email, uniq = pick_email(emails, domain)
    row["email"] = email
    row["all_emails"] = "; ".join(uniq[:8])

    hits = [t for t in terms() if t in blob]
    row["peptides"] = ", ".join(hits[:6])
    row["peptide_hits"] = str(len(hits))

    st = STATE_ZIP_RE.search(blob.upper())
    row["state"] = st.group(1) if st else ""
    ph = PHONE_RE.search(blob)
    row["phone"] = f"({ph.group(1)}) {ph.group(2)}-{ph.group(3)}" if ph else ""
    # A state sitting in front of a ZIP is a real US address. A US phone number
    # on its own is weaker but still a US signal. A bare five-digit number is
    # not -- it is usually a price, a year or a product code.
    row["us_signal"] = "yes" if (row["state"] or row["phone"]) else ""

    row["status"] = "ok" if email else "no_email"
    return row


def load(path, key="domain"):
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r[key]: r for r in csv.DictReader(f) if r.get(key)}


def save(path, by_domain):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for d in sorted(by_domain):
            w.writerow({k: by_domain[d].get(k, "") for k in COLS})


def main(limit=None, recheck=False):
    cands = load(CAND)
    if not cands:
        sys.exit("no candidates -- run medspa/discover.py first")
    done = load(ENR)
    todo = [d for d in cands if recheck or d not in done]
    if limit:
        todo = todo[:limit]
    if not todo:
        print(f"nothing to do (candidates={len(cands)}, enriched={len(done)})")
        return
    print(f"enriching {len(todo)} of {len(cands)} candidates...")
    for i, d in enumerate(todo, 1):
        row = enrich_one(d, cands[d].get("url", ""))
        if not row["business_name"]:
            row["business_name"] = cands[d].get("business_name", "")
        row["city"] = row["city"] or cands[d].get("city", "")
        row["state"] = row["state"] or cands[d].get("state", "")
        done[d] = row
        print(f"{i:>4}/{len(todo)}  {row['status']:<11} {d:<38} {row['email'] or '-':<34} "
              f"peps={row['peptide_hits']:<3} {row['state']}")
        if i % 10 == 0:
            save(ENR, done)
    save(ENR, done)
    from collections import Counter
    print("status:", dict(Counter(r["status"] for r in done.values())))
    print(f"wrote {ENR}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    rc = "--recheck" in args
    nums = [int(a) for a in args if a.isdigit()]
    main(nums[0] if nums else None, rc)
