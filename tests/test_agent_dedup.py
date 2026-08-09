from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import Lead
import pytest

from scraper.agent import DEFAULT_COMPANY_TYPES, build_queries, is_qualifying_lead, upsert_lead
from scraper.query_templates import QUERY_TEMPLATES
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
    site = SiteData(url="https://example.com", domain="example.com", company_type="research_only",
                    us_based=False, sells_direct=True)
    assert is_qualifying_lead(site, allow_non_us=False) == "skipped_non_us"


def test_is_qualifying_lead_allows_non_us_when_flagged():
    site = SiteData(url="https://example.com", domain="example.com", company_type="research_only",
                    us_based=False, sells_direct=True)
    assert is_qualifying_lead(site, allow_non_us=True) is None


def test_is_qualifying_lead_accepts_relevant_us_site():
    site = SiteData(url="https://example.com", domain="example.com", company_type="compounding_pharmacy",
                    us_based=True, sells_direct=True)
    assert is_qualifying_lead(site, allow_non_us=False) is None


def test_build_queries_covers_every_requested_company_type():
    queries = build_queries(["BPC-157"], max_queries=None, company_types=list(QUERY_TEMPLATES))
    assert any("research peptides supplier" in q for q in queries)
    assert any("compounding pharmacy" in q for q in queries)
    assert any("peptide manufacturer" in q for q in queries)
    assert any("peptides for sale" in q or "buy" in q for q in queries)


def test_build_queries_default_focus_excludes_pharmacy_and_lab():
    queries = build_queries(["BPC-157"], max_queries=None, company_types=DEFAULT_COMPANY_TYPES)
    assert any("research peptides supplier" in q for q in queries)
    assert any("peptides for sale" in q or "buy" in q for q in queries)
    assert not any("compounding pharmacy" in q for q in queries)
    assert not any("peptide manufacturer" in q for q in queries)


def test_build_queries_respects_max_queries_and_samples_requested_types():
    queries = build_queries(["BPC-157", "NAD+"], max_queries=4, company_types=list(QUERY_TEMPLATES))
    assert len(queries) == 4


def test_build_queries_rejects_unknown_company_type():
    with pytest.raises(ValueError):
        build_queries(["BPC-157"], max_queries=None, company_types=["not_a_real_type"])


def test_is_qualifying_lead_rejects_content_site():
    site = SiteData(url="https://blog.com", domain="blog.com", company_type="research_only",
                    us_based=True, is_content_site=True, sells_direct=False)
    assert is_qualifying_lead(site, allow_non_us=False, vendors_only=True) == "skipped_content_site"


def test_is_qualifying_lead_rejects_non_vendor():
    site = SiteData(url="https://info.com", domain="info.com", company_type="research_only",
                    us_based=True, sells_direct=False)
    assert is_qualifying_lead(site, allow_non_us=False, vendors_only=True) == "skipped_not_a_vendor"


def test_is_qualifying_lead_accepts_storefront():
    site = SiteData(url="https://shop.com", domain="shop.com", company_type="research_only",
                    us_based=True, sells_direct=True, manufactures=True)
    assert is_qualifying_lead(site, allow_non_us=False, vendors_only=True) is None


def test_is_qualifying_lead_keeps_non_vendor_when_opted_in():
    site = SiteData(url="https://info.com", domain="info.com", company_type="research_only",
                    us_based=True, sells_direct=False)
    assert is_qualifying_lead(site, allow_non_us=False, vendors_only=False) is None
