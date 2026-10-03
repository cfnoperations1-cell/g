#!/usr/bin/env python3
"""Turn the Instagram top-accounts markdown tables into resolved-CSV rows.

Jonathan supplied two hand-compiled lists -- top US med spas and top US
telehealth brands on Instagram -- as markdown tables with a contact column.
Rather than a second ingest path, this converts them into exactly the CSV
that ingest_resolved.py already reads, so every gate, dedupe rule and
template in the existing pipeline applies unchanged.

Two rules matter here and are enforced below:

  * An address is used only if the table prints one. Nothing is constructed
    from a company name or domain -- the campaign never guesses an address.
  * The med spa template says "I saw you offer X". X has to be true, so the
    peptide phrase is derived from the row's own Specialty/Description text
    with the same QUOTABLE / BRAND_TO_GENERIC / GLP1_CATEGORY logic
    lead_hunt.py uses. A listing that mentions no peptide and no GLP-1
    category gets no phrase, and ingest_resolved then drops it rather than
    claim something the listing does not support.
"""
import csv, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(ROOT / "outreach"))
import lead_hunt as lh
import serve_send as ss

FREEMAIL = ss.FREEMAIL

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Notes the compilers added beside an address, warning it is not a sales inbox.
NOT_SALES = re.compile(r"media contact|not general customer service|press only", re.I)


def cells(line):
    parts = [c.strip() for c in line.strip().strip("|").split("|")]
    return parts


def peptide_phrase(text):
    """The same derivation lead_hunt applies to a crawled page, on listing text."""
    blob = text.lower()
    quoted = [t for t in lh.QUOTABLE if t in blob]
    for brand, generic in lh.BRAND_TO_GENERIC.items():
        if brand in blob and generic not in quoted:
            quoted.append(generic)
    if not quoted and any(c in blob for c in lh.GLP1_CATEGORY):
        quoted.append(lh.GLP1_FALLBACK)
    return ", ".join(dict.fromkeys(quoted).keys()) if quoted else ""


def domain_of(url):
    m = re.search(r"https?://([^/\s)]+)", url or "")
    return m.group(1).lower().lstrip("www.") if m else ""


def parse(path, audience):
    rows, stats = [], {"rows": 0, "no email": 0, "not a sales inbox": 0,
                       "no quotable compound -> neutral template": 0,
                       "freemail with no usable name": 0}
    header = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        c = cells(line)
        if header is None:
            if any(x.lower() in ("name", "brand") for x in c):
                header = [x.lower() for x in c]
            continue
        if set("".join(c)) <= set("-: "):          # the |---|---| separator
            continue
        row = dict(zip(header, c))
        name = re.sub(r"\[|\]|\(.*?\)", "", row.get("name") or row.get("brand") or "").strip()
        if not name or name == "#":
            continue
        stats["rows"] += 1
        contact = row.get("contact (phone / email)") or row.get("contact") or ""
        site = row.get("website") or ""
        blurb = (row.get("specialty") or row.get("description") or "")
        found = EMAIL_RE.findall(contact)
        if not found:
            stats["no email"] += 1
            continue
        if NOT_SALES.search(contact):
            stats["not a sales inbox"] += 1
            continue
        email = found[0].lower()
        # The address is the primary datum here and the Website column is not:
        # it holds app.nexhealth.com for Med Aesthetics Miami and linktr.ee for
        # The Beaumont Med Spa, while their addresses are on their own domains.
        # Recording the address's own domain also makes ingest's same-site check
        # meaningful instead of comparing a real inbox against a booking platform,
        # and gives the per-location franchises (4everyoungscottsdale.com and its
        # siblings) distinct brand keys rather than collapsing them into one.
        em_dom = email.split("@", 1)[1]
        dom = domain_of(site) if em_dom in FREEMAIL else em_dom
        dom = dom or em_dom
        peps = peptide_phrase(f"{blurb} {name}")
        # Which template the row can honestly carry. The med spa one opens with
        # "I saw you offer X", so it is only available when the listing actually
        # names something we supply. Where it does not, the row still goes in --
        # these are US clinics and brands Jonathan chose -- but on the vendor
        # template, which describes only what WE do and asserts nothing about the
        # recipient. A one-line Instagram blurb not mentioning a compound is not
        # evidence the practice has none, unlike a full page crawl.
        # ingest_resolved runs the stored name through clean_vendor, which falls
        # back to the DOMAIN whenever the name does not resemble it -- a sound
        # rule for scraped page titles. For a freemail row with no website that
        # fallback is "gmail.com", which would go out as the subject line, so
        # those rows are dropped rather than sent under a mangled name. Three
        # rows across both files; every other name either survives intact or
        # falls back to a real company domain.
        import serve_send as _ss  # imported here to keep the module import-light
        if _ss.clean_vendor(name, dom) != name and dom.split(".")[-2:] == ["gmail", "com"]:
            stats["freemail with no usable name"] += 1
            continue
        aud = audience if peps else "vendor"
        if aud == "vendor":
            stats["no quotable compound -> neutral template"] += 1
        rows.append({"vendor": name, "domain": dom, "email": email, "status": "ok",
                     "audience": aud, "peptides": peps,
                     "source": Path(path).name})
    return rows, stats


def main():
    out = Path(sys.argv[1])
    allrows = []
    for path, aud in [(a, b) for a, b in zip(sys.argv[2::2], sys.argv[3::2])]:
        rows, stats = parse(path, aud)
        print(f"{Path(path).name}  audience={aud}")
        for k, v in stats.items():
            print(f"    {k:20s} {v}")
        print(f"    {'usable':20s} {len(rows)}")
        allrows += rows
    cols = ["vendor", "domain", "email", "status", "audience", "peptides", "source"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(allrows)
    print(f"\nwrote {len(allrows)} rows -> {out}")


if __name__ == "__main__":
    main()
