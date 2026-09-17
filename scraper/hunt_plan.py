"""Serve the next batch of discovery queries, without ever repeating one.

The daily lead hunt needs a steady supply of search queries that do not overlap
with yesterday's. This generates a deterministic plan from the metro list and
the peptide keyword list, then hands out the next N and remembers how far it
got in scraper/.hunt_cursor.

    python3 scraper/hunt_plan.py next 40      # print the next 40 queries, advance
    python3 scraper/hunt_plan.py peek 10      # print without advancing
    python3 scraper/hunt_plan.py stats        # how much of the plan is used
    python3 scraper/hunt_plan.py reset        # start over from the top
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CURSOR = HERE / ".hunt_cursor"

MEDSPA_TEMPLATES = [
    '{city} med spa semaglutide tirzepatide peptide therapy contact',
    '{city} wellness clinic peptide injections BPC-157 NAD+ contact us',
    '{city} medical weight loss clinic compounded semaglutide "contact"',
    '{city} anti-aging clinic peptide therapy sermorelin ipamorelin',
    '{city} aesthetics practice GLP-1 weight loss injections email',
]
VENDOR_TEMPLATES = [
    '"{peptide}" research peptides "research use only" buy USA COA',
    '"{peptide}" research peptides wholesale bulk pricing United States',
    '"{peptide}" peptide vendor third-party tested lyophilized "add to cart"',
]
LISTICLE_TEMPLATES = [
    'best research peptide companies ranked 2026 compared',
    'top US peptide vendors reviewed purity tested list',
    'research peptide vendor list reddit alternatives 2026',
]


def lines(name):
    return [l.strip() for l in (HERE / name).read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def plan():
    """A stable, interleaved query plan: med spa and vendor queries alternate so
    every day's batch feeds both campaigns rather than one at a time."""
    cities, peps = lines("us_metros.txt"), lines("peptide_keywords.txt")
    med = [t.format(city=c) for c in cities for t in MEDSPA_TEMPLATES]
    ven = [t.format(peptide=p) for p in peps for t in VENDOR_TEMPLATES]
    out, i, j = [], 0, 0
    while i < len(med) or j < len(ven):
        for _ in range(2):
            if i < len(med):
                out.append(("medspa", med[i])); i += 1
        if j < len(ven):
            out.append(("vendor", ven[j])); j += 1
    return out + [("vendor", q) for q in LISTICLE_TEMPLATES]


def cursor():
    try:
        return int(CURSOR.read_text().strip())
    except Exception:
        return 0


def main(argv):
    p = plan()
    cmd = argv[0] if argv else "stats"
    n = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 20
    c = cursor()
    if cmd == "stats":
        print(f"plan: {len(p)} queries  used: {c}  left: {len(p) - c}")
        return
    if cmd == "reset":
        CURSOR.write_text("0")
        print("cursor reset")
        return
    batch = p[c:c + n]
    if not batch:
        print("# plan exhausted -- widen scraper/us_metros.txt or the templates, then reset")
        return
    for aud, q in batch:
        print(f"{aud}\t{q}")
    if cmd == "next":
        CURSOR.write_text(str(c + len(batch)))


if __name__ == "__main__":
    main(sys.argv[1:])
