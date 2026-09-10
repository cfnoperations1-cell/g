import csv

from scraper.peptiprices_roster import parse_suppliers, run

HTML = """
<a href="/about">About</a>
<a href="https://www.peptiprices.com/prices">Prices</a>
<a href="https://blue-sky-peptide.com/products/bpc">Buy</a>
<a href="https://acmepeptides.com/">Acme Peptides</a>
<a href="https://acmepeptides.com/bpc-157">https://acmepeptides.com/bpc-157</a>
<a href="https://www.reddit.com/r/peptides">reddit</a>
<a href="https://t.me/somevendor">telegram</a>
"""


def test_parse_suppliers_one_per_host_with_sensible_names():
    assert parse_suppliers(HTML) == [
        ("Acme Peptides", "https://acmepeptides.com/"),
        ("Blue Sky Peptide", "https://blue-sky-peptide.com/"),
    ]


def test_run_skips_vendors_already_present_by_name_or_host(tmp_path):
    src = tmp_path / "v.csv"
    src.write_text("Vendor,Website,Email\nACME peptides,,\nOther,blue-sky-peptide.com,\n")
    out = tmp_path / "out.csv"
    stats = run(src, out, html=HTML)
    assert stats == {"existing": 2, "suppliers_on_page": 2, "added": 0}

    src.write_text("Vendor,Website,Email\nSomeone Else,https://x.com/,\n")
    stats = run(src, out, html=HTML)
    assert stats["added"] == 2
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert rows[1]["Vendor"] == "Acme Peptides" and rows[1]["Listed On"] == "PeptiPrices"
    assert rows[2]["Website"] == "https://blue-sky-peptide.com/"
