from pathlib import Path

from scraper.resolve_vendors import load_vendor_names, pick_domain, score_domain


def test_score_domain_matches_company_name():
    assert score_domain("Blue Sky Peptide", "blueskypeptide.com") > 0.9


def test_score_domain_low_for_unrelated():
    assert score_domain("Blue Sky Peptide", "randomhealthblog.com") < 0.5


def test_pick_domain_selects_matching_site():
    urls = [
        "https://peptidebase.io/research-vendors/blue-sky-peptide",
        "https://www.reddit.com/r/peptides/comments/xyz",
        "https://blueskypeptide.com/products",
    ]
    assert pick_domain("Blue Sky Peptide", urls, 0.55) == "blueskypeptide.com"


def test_pick_domain_rejects_directory_only_results():
    urls = ["https://peptidebase.io/research-vendors/ghost-labs", "https://x.com/ghostlabs"]
    assert pick_domain("Ghost Labs", urls, 0.55) is None


def test_pick_domain_returns_none_below_threshold():
    assert pick_domain("Acme Peptides", ["https://totallyunrelated.com"], 0.55) is None


def test_load_vendor_names_parses_tsv(tmp_path):
    f = tmp_path / "v.tsv"
    f.write_text("# header\nAcme Peptides\tUnited States\nBeta Labs\tCanada\n\n")
    assert load_vendor_names(f) == [("Acme Peptides", "United States"), ("Beta Labs", "Canada")]


def test_real_roster_file_is_parseable():
    rows = load_vendor_names(Path("scraper/vendor_names.tsv"))
    assert len(rows) > 150
    assert all(name and country for name, country in rows)
