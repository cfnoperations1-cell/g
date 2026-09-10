from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Lead
from scraper.import_peptidebase import (
    Provider,
    find_website,
    is_challenge_page,
    load_providers,
    page_url,
    parse_listing_page,
    parse_vendor_table,
    run,
)

LISTING_HTML = """
<!-- saved from url=(0040)https://peptidebase.io/directory/telehealth -->
<html><head><title>Telehealth | PeptideBase</title></head><body>
<nav><a href="/providers/">All providers</a></nav>
<a href="/providers/acme-health">
  <span class="type">Telehealth</span>
  <h3>Acme Health</h3>
  <span class="loc">Austin, United States</span>
  <span>4.8</span><span>(120)</span><span>Google</span>
  <span>View →</span>
</a>
<a href="/providers/north-peptide-clinic">
  <span>Clinic</span><span>Regulatory warning</span>
  <div>North Peptide Clinic</div>
  <div>Salt Lake City, United States</div>
  <span>4.2</span><span>17</span><span>Google</span><span>View →</span>
</a>
<a href="/providers/maple-rx"><img src="logo.png" alt=""></a>
<a href="/providers/maple-rx">Pharmacy (503A) Maple Rx Toronto, Canada 4.9 88 Google View →</a>
<a href="/providers/acme-health?utm=1"><span>Acme Health</span></a>
</body></html>
"""

VENDOR_TABLE_HTML = """
<!-- saved from url=(0038)https://peptidebase.io/research-vendors -->
<html><body><table>
<tr><th>Vendor</th><th>Country</th><th>Rating</th><th>Status</th></tr>
<tr><td><a href="/research-vendors/blue-sky">Blue Sky Peptide</a></td><td>United States</td><td>9.1</td><td>Verified</td></tr>
<tr><td><a href="https://www.betalabs.com/">Beta Labs</a></td><td>Latvia</td><td>7.4</td><td>Under Review</td></tr>
</table></body></html>
"""

PROFILE_HTML = """
<!-- saved from url=(0041)https://peptidebase.io/providers/acme-health -->
<html><head><title>Acme Health | PeptideBase</title></head><body>
<h1>Acme Health</h1>
<a href="https://maps.google.com/?q=acme">Map</a>
<a href="https://www.trustpilot.com/review/acmehealth.com">Reviews</a>
<a href="https://www.acmehealth.com/?ref=peptidebase" rel="nofollow">Visit website</a>
<a href="/directory/telehealth">Back</a>
</body></html>
"""

CHALLENGE_HTML = """
<html><head><title>Just a moment...</title></head>
<body><div id="challenge-platform">Checking your browser</div></body></html>
"""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def test_parse_listing_cards_split_type_name_location_rating():
    rows = {p.name: p for p in parse_listing_page(_soup(LISTING_HTML))}
    assert set(rows) == {"Acme Health", "North Peptide Clinic", "Maple Rx"}

    acme = rows["Acme Health"]
    assert acme.type == "Telehealth"
    assert acme.category == "telehealth"
    assert acme.location == "Austin, United States"
    assert acme.rating == "4.8"
    assert acme.reviews == "120"
    assert acme.flag == ""
    assert acme.profile_url == "https://peptidebase.io/providers/acme-health"
    assert acme.us_based is True

    north = rows["North Peptide Clinic"]
    assert north.type == "Clinic"
    assert north.flag == "Regulatory warning"
    assert north.location == "Salt Lake City, United States"
    assert north.reviews == "17"


def test_parse_listing_card_from_a_single_text_run():
    """A card whose markup gives no element boundaries still yields a name."""
    maple = {p.name: p for p in parse_listing_page(_soup(LISTING_HTML))}["Maple Rx"]
    assert maple.type == "Pharmacy (503A)"
    assert maple.category == "compounding_pharmacies"
    assert maple.location == "Toronto, Canada"
    assert maple.rating == "4.9"
    assert maple.reviews == "88"
    assert maple.us_based is False


def test_listing_dedupes_repeated_links_to_one_profile():
    names = [p.name for p in parse_listing_page(_soup(LISTING_HTML))]
    assert names.count("Acme Health") == 1
    assert names.count("Maple Rx") == 1


def test_parse_vendor_table_uses_headers_and_direct_links():
    rows = {p.name: p for p in parse_vendor_table(_soup(VENDOR_TABLE_HTML))}
    assert set(rows) == {"Blue Sky Peptide", "Beta Labs"}
    blue = rows["Blue Sky Peptide"]
    assert blue.type == "Research Vendor"
    assert blue.location == "United States"
    assert blue.rating == "9.1"
    assert blue.profile_url == "https://peptidebase.io/research-vendors/blue-sky"
    assert blue.website == ""
    beta = rows["Beta Labs"]
    assert beta.website == "https://www.betalabs.com/"
    assert beta.flag == "Under Review"
    assert beta.us_based is False


def test_find_website_takes_the_labelled_link_not_maps_or_review_sites():
    assert find_website(_soup(PROFILE_HTML)) == "https://www.acmehealth.com/"


def test_page_url_prefers_browser_saved_from_comment(tmp_path):
    assert page_url(PROFILE_HTML, _soup(PROFILE_HTML)) == "https://peptidebase.io/providers/acme-health"
    canonical = '<html><head><link rel="canonical" href="https://peptidebase.io/directory/clinics"></head></html>'
    assert page_url(canonical, _soup(canonical)) == "https://peptidebase.io/directory/clinics"
    bare = "<html><body></body></html>"
    assert page_url(bare, _soup(bare), tmp_path / "compounding-pharmacies.html").endswith("/directory/compounding-pharmacies")


def test_challenge_page_is_recognised_and_skipped(tmp_path):
    assert is_challenge_page(CHALLENGE_HTML, _soup(CHALLENGE_HTML))
    f = tmp_path / "telehealth.html"
    f.write_text(CHALLENGE_HTML)
    assert load_providers([f]) == []


def test_profile_page_supplies_website_for_listing_card(tmp_path):
    (tmp_path / "telehealth.html").write_text(LISTING_HTML)
    (tmp_path / "acme-health.html").write_text(PROFILE_HTML)
    providers = {p.name: p for p in load_providers(sorted(tmp_path.iterdir()))}
    acme = providers["Acme Health"]
    assert acme.website == "https://www.acmehealth.com/"
    assert acme.type == "Telehealth"            # from the card
    assert acme.location == "Austin, United States"
    assert len(providers) == 3                   # profile merged, not duplicated


def test_note_carries_directory_metadata():
    p = Provider(name="Acme", type="Telehealth", location="Austin, United States",
                 rating="4.8", reviews="120", flag="Regulatory warning",
                 profile_url="https://peptidebase.io/providers/acme")
    assert p.note() == ("PeptideBase: Telehealth · Austin, United States · rating 4.8 (120 Google reviews) · "
                        "Regulatory warning · https://peptidebase.io/providers/acme")


def _memory_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    import db as db_module
    monkeypatch.setattr(db_module, "SessionLocal", Session)
    monkeypatch.setattr(db_module, "init_db", lambda: None)
    return Session


def test_run_writes_csv_creates_leads_and_rosters_name_only_providers(tmp_path, monkeypatch):
    Session = _memory_db(monkeypatch)
    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "telehealth.html").write_text(LISTING_HTML)
    (saved / "acme-health.html").write_text(PROFILE_HTML)
    (saved / "research-vendors.html").write_text(VENDOR_TABLE_HTML)
    csv_path = tmp_path / "out.csv"
    names = tmp_path / "names.tsv"
    names.write_text("# name\tcountry\nMaple Rx\tCanada\n")

    stats = run([saved], csv_path=csv_path, names_file=names, do_enrich=False)

    assert stats["providers"] == 5
    assert stats["with_website"] == 2
    assert stats["added"] == 2

    csv_text = csv_path.read_text(encoding="utf-8-sig")
    assert csv_text.splitlines()[0] == "category,type,name,location,rating,reviews,flag,website,profile_url"
    assert "Acme Health" in csv_text and "Blue Sky Peptide" in csv_text

    session = Session()
    leads = {lead.domain: lead for lead in session.query(Lead)}
    assert set(leads) == {"acmehealth.com", "betalabs.com"}
    acme = leads["acmehealth.com"]
    assert acme.source == "peptidebase"
    assert acme.company_name == "Acme Health"
    assert acme.us_based is True
    assert acme.company_type is None           # telehealth isn't one of the CRM's types
    assert "Telehealth · Austin, United States · rating 4.8 (120 Google reviews)" in acme.notes
    assert leads["betalabs.com"].us_based is False
    assert "Under Review" in leads["betalabs.com"].notes

    # Name-only providers go to the roster, skipping ones already there.
    roster = names.read_text()
    assert "North Peptide Clinic\tUnited States" in roster
    assert "Blue Sky Peptide\tUnited States" in roster
    assert roster.count("Maple Rx") == 1
    assert stats["roster_added"] == 2


def test_run_is_idempotent_and_keeps_existing_lead_data(tmp_path, monkeypatch):
    Session = _memory_db(monkeypatch)
    session = Session()
    session.add(Lead(company_name="ACME (from search)", website="https://acmehealth.com/",
                     domain="acmehealth.com", source="serper", email="hello@acmehealth.com",
                     notes="called them once", status="contacted"))
    session.commit()

    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "telehealth.html").write_text(LISTING_HTML)
    (saved / "acme-health.html").write_text(PROFILE_HTML)
    names = tmp_path / "names.tsv"
    names.write_text("")

    first = run([saved], csv_path=tmp_path / "a.csv", names_file=names, do_enrich=False)
    second = run([saved], csv_path=tmp_path / "b.csv", names_file=names, do_enrich=False)

    assert first["added"] == 0 and first["updated"] == 1
    assert second["updated"] == 1
    assert second["roster_added"] == 0
    lead = Session().query(Lead).filter_by(domain="acmehealth.com").one()
    assert lead.source == "serper+peptidebase"
    assert lead.email == "hello@acmehealth.com"
    assert lead.status == "contacted"
    assert lead.notes.startswith("called them once\nPeptideBase:")
    assert lead.notes.count("PeptideBase:") == 1


def test_pharmacy_type_maps_to_compounding_pharmacy(tmp_path, monkeypatch):
    Session = _memory_db(monkeypatch)
    listing = """
    <!-- saved from url=(0052)https://peptidebase.io/directory/compounding-pharmacies -->
    <html><body><a href="/providers/maple-rx"><span>Pharmacy (503B)</span><h3>Maple Rx</h3>
    <span>Denver, United States</span></a></body></html>"""
    profile = """
    <!-- saved from url=(0038)https://peptidebase.io/providers/maple-rx -->
    <html><body><h1>Maple Rx</h1><a href="https://maplerx.com">Website</a></body></html>"""
    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "pharmacies.html").write_text(listing)
    (saved / "maple.html").write_text(profile)

    run([saved], csv_path=tmp_path / "o.csv", names_file=tmp_path / "n.tsv", do_enrich=False)
    lead = Session().query(Lead).one()
    assert lead.company_type == "compounding_pharmacy"
    assert lead.website == "https://maplerx.com/"


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    Session = _memory_db(monkeypatch)
    (tmp_path / "telehealth.html").write_text(LISTING_HTML)
    csv_path = tmp_path / "o.csv"
    names = tmp_path / "n.tsv"
    stats = run([tmp_path / "telehealth.html"], csv_path=csv_path, names_file=names, do_enrich=False, dry_run=True)
    assert stats["providers"] == 3
    assert not csv_path.exists()
    assert not names.exists()
    assert Session().query(Lead).count() == 0
