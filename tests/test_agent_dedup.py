from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Lead
from scraper.agent import upsert_lead
from scraper.site_parser import SiteData


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_upsert_adds_new_lead():
    session = make_session()
    site = SiteData(
        url="https://example.com",
        domain="example.com",
        company_name="Example Co",
        email="sales@example.com",
        research_only_evidence="research use only",
    )
    result = upsert_lead(session, site, source="google_cse", matched_query="q", require_research_only=True)
    assert result == "added"
    assert session.query(Lead).count() == 1


def test_upsert_skips_duplicate_domain():
    session = make_session()
    site = SiteData(url="https://example.com", domain="example.com", company_name="Example Co", research_only_evidence="research use only")
    upsert_lead(session, site, source="google_cse", matched_query="q", require_research_only=True)
    result = upsert_lead(session, site, source="google_cse", matched_query="q", require_research_only=True)
    assert result == "duplicate"
    assert session.query(Lead).count() == 1


def test_upsert_skips_non_research_only_by_default():
    session = make_session()
    site = SiteData(url="https://example.com", domain="example.com", company_name="Example Co", research_only_evidence=None)
    result = upsert_lead(session, site, source="google_cse", matched_query="q", require_research_only=True)
    assert result == "skipped_not_research_only"
    assert session.query(Lead).count() == 0


def test_upsert_allows_non_research_only_when_disabled():
    session = make_session()
    site = SiteData(url="https://example.com", domain="example.com", company_name="Example Co", research_only_evidence=None)
    result = upsert_lead(session, site, source="google_cse", matched_query="q", require_research_only=False)
    assert result == "added"
    assert session.query(Lead).count() == 1
