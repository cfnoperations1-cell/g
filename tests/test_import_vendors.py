from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Lead
from scraper.import_vendors import run
from scraper.resolve_vendors import load_domain_map, write_domain_map


def test_write_and_load_domain_map_roundtrip(tmp_path):
    f = tmp_path / "map.tsv"
    write_domain_map([("Acme Peptides", "United States", "acmepeptides.com")], f)
    assert load_domain_map(f) == {"Acme Peptides": ("United States", "acmepeptides.com")}


def test_write_domain_map_merges_without_losing_rows(tmp_path):
    f = tmp_path / "map.tsv"
    write_domain_map([("A", "United States", "a.com")], f)
    write_domain_map([("B", "Canada", "b.com")], f)
    loaded = load_domain_map(f)
    assert loaded == {"A": ("United States", "a.com"), "B": ("Canada", "b.com")}


def test_import_keeps_every_vendor_even_when_site_is_unreachable(tmp_path, monkeypatch):
    """The whole point: a curated roster entry is a lead regardless of whether
    its website can be fetched."""
    names = tmp_path / "names.tsv"
    names.write_text("Acme Peptides\tUnited States\nBeta Labs\tCanada\n")
    mapping = tmp_path / "map.tsv"
    write_domain_map(
        [("Acme Peptides", "United States", "acmepeptides.com"),
         ("Beta Labs", "Canada", "betalabs.com")], mapping)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("scraper.import_vendors.SessionLocal", Session)
    monkeypatch.setattr("scraper.import_vendors.init_db", lambda: None)

    stats = run(names, mapping, country=None, do_enrich=False,
                threshold=0.55, limit=None, dry_run=False)

    assert stats["added"] == 2
    session = Session()
    leads = {lead.domain: lead for lead in session.query(Lead)}
    assert set(leads) == {"acmepeptides.com", "betalabs.com"}
    # country from the roster drives us_based, no site visit needed
    assert leads["acmepeptides.com"].us_based is True
    assert leads["betalabs.com"].us_based is False


def test_import_is_idempotent(tmp_path, monkeypatch):
    names = tmp_path / "names.tsv"
    names.write_text("Acme Peptides\tUnited States\n")
    mapping = tmp_path / "map.tsv"
    write_domain_map([("Acme Peptides", "United States", "acmepeptides.com")], mapping)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("scraper.import_vendors.SessionLocal", Session)
    monkeypatch.setattr("scraper.import_vendors.init_db", lambda: None)

    run(names, mapping, None, False, 0.55, None, False)
    second = run(names, mapping, None, False, 0.55, None, False)

    assert second["added"] == 0
    assert second["updated"] == 1
    assert Session().query(Lead).count() == 1


def test_import_counts_vendors_with_no_website(tmp_path, monkeypatch):
    names = tmp_path / "names.tsv"
    names.write_text("Ghost Corp\tUnited States\n")
    mapping = tmp_path / "map.tsv"
    write_domain_map([], mapping)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("scraper.import_vendors.SessionLocal", Session)
    monkeypatch.setattr("scraper.import_vendors.init_db", lambda: None)
    monkeypatch.setattr("scraper.import_vendors.first_configured_provider", lambda: None)

    stats = run(names, mapping, None, False, 0.55, None, False)
    assert stats["no_domain"] == 1
