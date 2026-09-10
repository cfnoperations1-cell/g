import csv
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Lead
from scraper.enrich_csv import ADDED_COLUMNS, enrich_row, import_to_crm, name_column, run
from scraper.finnrick import FinnrickContacts
from scraper.search_providers import SearchResult
from scraper.site_parser import SiteData


class FakeFinnrick:
    def __init__(self, table):
        self.table = table

    def index_size(self):
        return len(self.table)

    def lookup(self, slug=None, name=None):
        return self.table.get(slug) or self.table.get(name)


class FakeResolver:
    name = "fake"

    def __init__(self, urls):
        self.urls = urls

    def search(self, query, n):
        for u in self.urls:
            yield SearchResult(url=u, title="", snippet="", query=query)


def _row(**overrides):
    base = {"Vendor": "Acme Peptides", "Country": "", "Finnrick Profile": "", "Website": "", "Email": "",
            "WhatsApp": "", "Telegram/Signal": "", "Phone": "", "Social": "", "Email Source": "", "Notes": ""}
    base.update(overrides)
    return base


def test_name_column_detection():
    assert name_column(["Vendor", "Website"]) == "Vendor"
    assert name_column(["Company", "x"]) == "Company"


def test_finnrick_fills_blanks_but_never_overwrites(monkeypatch):
    contacts = FinnrickContacts(slug="acme-peptides", name="Acme Peptides", website="https://acmepeptides.com/",
                                websites=["https://acmepeptides.com/"], emails=["hi@gmail.com", "sales@acmepeptides.com"],
                                whatsapp=["+15550001111"], telegram=["@acme"], signal=["acme.01"])
    monkeypatch.setattr("scraper.enrich_csv.parse_site", lambda url, kw: SiteData(url=url, domain="acmepeptides.com"))
    row = _row(Email="existing@acmepeptides.com", Finnrick_Profile="")
    out = enrich_row(row, "Vendor", FakeFinnrick({"Acme Peptides": contacts}), {}, None, visit=False, keywords=[])

    assert out["Website"] == "https://acmepeptides.com/"
    assert out["Website Source"] == "finnrick"
    assert out["Email"] == "existing@acmepeptides.com"          # existing value kept
    assert out["Email Source"] == ""                              # nothing filled, so untouched
    assert out["WhatsApp"] == "+15550001111"
    assert out["Telegram/Signal"] == "@acme; Signal acme.01"
    assert out["Finnrick Profile"] == "https://www.finnrick.com/vendors/acme-peptides"
    assert out["Site Status"] == "not visited"
    assert "website<-finnrick" in out["Enrichment"]


def test_finnrick_email_prefers_vendor_domain_and_records_source():
    contacts = FinnrickContacts(slug="acme", name="Acme", website="https://acmepeptides.com/",
                                emails=["acme@proton.me", "sales@acmepeptides.com"])
    out = enrich_row(_row(), "Vendor", FakeFinnrick({"Acme Peptides": contacts}), {}, None, visit=False, keywords=[])
    assert out["Email"] == "sales@acmepeptides.com"
    assert out["Email Source"] == "finnrick.com"


def test_domain_map_then_search_fill_website_in_order():
    out = enrich_row(_row(), "Vendor", None, {"acmepeptides": "acmepeptides.com"}, FakeResolver(["https://wrong.com"]),
                     visit=False, keywords=[])
    assert out["Website"] == "https://acmepeptides.com/"
    assert out["Website Source"] == "vendor_domains.tsv"

    out = enrich_row(_row(), "Vendor", None, {}, FakeResolver(["https://reddit.com/r/x", "https://acmepeptides.com/shop"]),
                     visit=False, keywords=[])
    assert out["Website"] == "https://acmepeptides.com/"
    assert out["Website Source"] == "search:fake"

    out = enrich_row(_row(), "Vendor", None, {}, FakeResolver(["https://totallyunrelated.com"]), visit=False, keywords=[])
    assert out["Website"] == ""
    assert out["Site Status"] == "no website"
    assert out["Enrichment"] == "nothing new"


def test_site_visit_fills_email_phone_instagram_and_classification(monkeypatch):
    def fake_parse(url, keywords):
        return SiteData(url=url, domain="acmepeptides.com", email="info@acmepeptides.com", phone="(555) 010-2000",
                        instagram="@acme", company_type="research_only", state="Texas", pages_checked=[url])

    monkeypatch.setattr("scraper.enrich_csv.parse_site", fake_parse)
    out = enrich_row(_row(Website="acmepeptides.com"), "Vendor", None, {}, None, visit=True, keywords=["bpc-157"])
    assert out["Website Source"] == "original"
    assert out["Site Status"] == "reached"
    assert out["Email"] == "info@acmepeptides.com"
    assert out["Email Source"] == "acmepeptides.com"
    assert out["Phone"] == "(555) 010-2000"
    assert out["Instagram"] == "@acme"
    assert out["Social"] == "Instagram @acme"
    assert out["Peptide Confirmed"] == "yes"
    assert out["Company Type"] == "research_only"
    assert out["Detected US State"] == "Texas"


def test_unreachable_site_leaves_cells_empty(monkeypatch):
    monkeypatch.setattr("scraper.enrich_csv.parse_site", lambda url, kw: SiteData(url=url, domain="acmepeptides.com"))
    out = enrich_row(_row(Website="https://acmepeptides.com/"), "Vendor", None, {}, None, visit=True, keywords=[])
    assert out["Site Status"] == "unreachable"
    assert out["Email"] == ""
    assert out["Peptide Confirmed"] == ""


def test_run_writes_csv_with_added_columns_and_resumes(tmp_path, monkeypatch):
    src = tmp_path / "vendors.csv"
    src.write_text("Vendor,Website,Email\nAcme Peptides,acmepeptides.com,\nBeta Labs,,\n")
    out = tmp_path / "out.csv"
    monkeypatch.setattr("scraper.enrich_csv.FinnrickClient", lambda: FakeFinnrick({}))
    monkeypatch.setattr("scraper.enrich_csv.first_configured_provider", lambda: None)
    monkeypatch.setattr("scraper.enrich_csv.load_domain_map", lambda p: {"Beta Labs": ("Canada", "betalabs.com")})
    visited = []

    def fake_parse(url, keywords):
        visited.append(url)
        return SiteData(url=url, domain=url.split("/")[2], email=f"hi@{url.split('/')[2]}", pages_checked=[url])

    monkeypatch.setattr("scraper.enrich_csv.parse_site", fake_parse)

    stats = run(src, out, pause_seconds=0)
    assert stats["rows"] == 2 and stats["website_filled"] == 1 and stats["email_filled"] == 2
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert [r["Vendor"] for r in rows] == ["Acme Peptides", "Beta Labs"]
    assert rows[1]["Website"] == "https://betalabs.com/"
    assert rows[1]["Email"] == "hi@betalabs.com"
    assert all(c in rows[0] for c in ADDED_COLUMNS)
    assert len(visited) == 2

    # Resume: nothing is re-visited, rows are carried over.
    stats = run(src, out, resume=True, pause_seconds=0)
    assert stats["reused"] == 2
    assert len(visited) == 2


def test_import_to_crm_adds_and_fills(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("db.SessionLocal", Session)
    monkeypatch.setattr("db.init_db", lambda: None)

    session = Session()
    session.add(Lead(company_name="Old", website="https://old.com/", domain="old.com", source="search", status="new"))
    session.commit()

    rows = [
        _row(Vendor="Acme Peptides", Website="https://acmepeptides.com/", Email="sales@acmepeptides.com",
             Country="United States", **{"Company Type": "research_only", "Listed On": "Finnrick"}),
        _row(Vendor="Old Co", Website="old.com", Email="hello@old.com", **{"Detected US State": "NV"}),
        _row(Vendor="No Site"),
    ]
    stats = import_to_crm(rows, "Vendor")
    assert stats == {"added": 1, "updated": 1, "no_website": 1}
    leads = {l.domain: l for l in session.query(Lead)}
    assert leads["acmepeptides.com"].email == "sales@acmepeptides.com"
    assert leads["acmepeptides.com"].us_based is True
    assert leads["acmepeptides.com"].company_type == "research_only"
    assert leads["acmepeptides.com"].source == "vendor_csv"
    assert "Listed On: Finnrick" in leads["acmepeptides.com"].notes
    assert leads["old.com"].email == "hello@old.com"
    assert leads["old.com"].source == "search+vendor_csv"
    assert leads["old.com"].us_based is True
