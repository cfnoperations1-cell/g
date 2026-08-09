from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Lead
from scraper.agent import build_queries, is_qualifying_lead, upsert_lead
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
        company_type="research_only",
        us_based=True,
        state="Texas",
    )
    result = upsert_lead(session, site, source="google_cse", matched_query="q")
    assert result == "added"
    lead = session.query(Lead).one()
    assert lead.company_type == "research_only"
    assert lead.us_based is True
    assert lead.state == "Texas"


def test_upsert_skips_duplicate_domain():
    session = make_session()
    site = SiteData(url="https://example.com", domain="example.com", company_name="Example Co", company_type="research_only", us_based=True)
    upsert_lead(session, site, source="google_cse", matched_query="q")
    result = upsert_lead(session, site, source="google_cse", matched_query="q")
    assert result == "duplicate"
    assert session.query(Lead).count() == 1


def test_is_qualifying_lead_rejects_irrelevant_site():
    site = SiteData(url="https://example.com", domain="example.com", company_type=None, us_based=True)
    assert is_qualifying_lead(site, allow_non_us=False) == "skipped_not_relevant"


def test_is_qualifying_lead_rejects_non_us_by_default():
    site = SiteData(url="https://example.com", domain="example.com", company_type="research_only", us_based=False)
    assert is_qualifying_lead(site, allow_non_us=False) == "skipped_non_us"


def test_is_qualifying_lead_allows_non_us_when_flagged():
    site = SiteData(url="https://example.com", domain="example.com", company_type="research_only", us_based=False)
    assert is_qualifying_lead(site, allow_non_us=True) is None


def test_is_qualifying_lead_accepts_relevant_us_site():
    site = SiteData(url="https://example.com", domain="example.com", company_type="compounding_pharmacy", us_based=True)
    assert is_qualifying_lead(site, allow_non_us=False) is None


def test_build_queries_covers_every_company_type():
    queries = build_queries(["BPC-157"], max_queries=None)
    assert any("research peptides supplier" in q for q in queries)
    assert any("compounding pharmacy" in q for q in queries)
    assert any("peptide manufacturer" in q for q in queries)
    assert any("peptides for sale" in q or "buy" in q for q in queries)


def test_build_queries_respects_max_queries_and_samples_all_types():
    queries = build_queries(["BPC-157", "NAD+"], max_queries=4)
    assert len(queries) == 4
