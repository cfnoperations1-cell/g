from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from clinics.agent import (
    City,
    build_directory_queries,
    build_query_space,
    leads_added_today,
    load_cities,
    qualifies,
    read_cursor,
    save_cursor,
    save_clinic,
)
from clinics.query_templates import DIRECTORY_TEMPLATES, SEARCH_TEMPLATES
from db import Base
from models import Lead, LeadKind
from scraper.site_parser import SiteData

CITIES = [City(("Austin", "TX")), City(("Miami", "FL"))]
SERVICES = ["peptide therapy", "semaglutide"]


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_load_cities_skips_comments_and_duplicates(tmp_path):
    path = tmp_path / "cities.txt"
    path.write_text("# a comment\nAustin\tTX\nAustin\tTX\nMiami\tfl\n\n")
    assert load_cities(path) == [("Austin", "TX"), ("Miami", "FL")]


def test_query_space_is_city_major():
    """A partial run must sample the whole country, not one metro."""
    space = build_query_space(CITIES, SERVICES, templates=['"{service}" clinic {city} {state}'])
    cities_in_order = [city for _, city in space]
    assert cities_in_order == ["Austin", "Austin", "Miami", "Miami"]


def test_city_only_templates_emit_one_query_per_city():
    space = build_query_space(CITIES, SERVICES, templates=["peptide clinic {city} {state}"])
    assert [query for query, _ in space] == ["peptide clinic Austin TX", "peptide clinic Miami FL"]


def test_query_space_is_large_enough_for_daily_rotation():
    """400 leads/day only works if the agent doesn't run out of new searches."""
    space = build_query_space(CITIES * 200, SERVICES * 10, templates=SEARCH_TEMPLATES)
    assert len(space) > 50_000


def test_directory_queries_cover_every_template():
    queries = build_directory_queries(CITIES)
    assert len(queries) == len(CITIES) * len(DIRECTORY_TEMPLATES)
    assert ("peptide therapy clinic in Austin, TX", "Austin") in queries


def test_cursor_round_trips():
    session = make_session()
    assert read_cursor(session) == 0
    save_cursor(session, 275, queries_run=25)
    assert read_cursor(session) == 275
    # A second run accumulates rather than overwriting the query count.
    save_cursor(session, 300, queries_run=25)
    assert read_cursor(session) == 300


def make_clinic_site(domain="clinic.com", us_based=True):
    return SiteData(
        url=f"https://{domain}",
        domain=domain,
        company_name="Test Clinic",
        email="info@clinic.com",
        us_based=us_based,
        state="Texas",
    )


DETAILS = {
    "clinic_type": "hormone_clinic",
    "peptide_evidence": "we offer peptide therapy",
    "peptides_offered": ["BPC-157", "Sermorelin"],
    "telehealth": True,
    "city": "Austin",
}


def test_save_clinic_writes_a_clinic_lead():
    session = make_session()
    assert save_clinic(session, make_clinic_site(), DETAILS, "serper", "peptide therapy Austin TX") == "added"

    lead = session.query(Lead).one()
    assert lead.kind == LeadKind.CLINIC.value
    assert lead.clinic_type == "hormone_clinic"
    assert lead.city == "Austin"
    assert lead.peptides_offered == "BPC-157, Sermorelin"
    assert lead.telehealth is True
    assert lead.research_only_evidence == "we offer peptide therapy"


def test_save_clinic_dedupes_against_an_existing_domain():
    session = make_session()
    save_clinic(session, make_clinic_site(), DETAILS, "serper", "q")
    assert save_clinic(session, make_clinic_site(), DETAILS, "brave", "q2") == "duplicate"
    assert session.query(Lead).count() == 1


def test_save_clinic_dedupes_against_a_vendor_on_the_same_domain():
    """A vendor already in the CRM must not be re-added as a clinic."""
    session = make_session()
    session.add(Lead(company_name="Peptide Co", website="https://clinic.com", domain="clinic.com",
                     source="seed", kind=LeadKind.VENDOR.value))
    session.commit()
    assert save_clinic(session, make_clinic_site(), DETAILS, "serper", "q") == "duplicate"


def test_qualifies_rejects_non_us_by_default():
    site = make_clinic_site(us_based=False)
    assert qualifies(site, None, allow_non_us=False) == "skipped_non_us"
    assert qualifies(site, None, allow_non_us=True) is None


def test_qualifies_passes_through_the_classifier_reason():
    assert qualifies(make_clinic_site(), "skipped_no_peptides", False) == "skipped_no_peptides"


def test_daily_target_counts_only_todays_clinic_leads():
    """The target is idempotent: a second run the same day tops up the shortfall."""
    session = make_session()
    now = datetime.utcnow().replace(hour=12)
    yesterday = now - timedelta(days=1)

    session.add_all([
        Lead(company_name="Today A", website="https://a.com", domain="a.com", source="s",
             kind=LeadKind.CLINIC.value, created_at=now),
        Lead(company_name="Today B", website="https://b.com", domain="b.com", source="s",
             kind=LeadKind.CLINIC.value, created_at=now),
        Lead(company_name="Yesterday", website="https://c.com", domain="c.com", source="s",
             kind=LeadKind.CLINIC.value, created_at=yesterday),
        Lead(company_name="Vendor today", website="https://d.com", domain="d.com", source="s",
             kind=LeadKind.VENDOR.value, created_at=now),
    ])
    session.commit()

    assert leads_added_today(session, now=now) == 2


def test_process_dedupes_filters_aggregators_and_saves(monkeypatch):
    """The fetch/classify/save path: one fetch per domain, directories never
    become leads, and a domain already in the CRM is never fetched at all."""
    from clinics import agent as clinics_agent

    session = make_session()
    session.add(Lead(company_name="Known", website="https://known.com", domain="known.com",
                     source="s", kind=LeadKind.CLINIC.value))
    session.commit()

    visited = []

    def fake_visit(url, peptide_keywords, city):
        visited.append(url)
        domain = url.split("//", 1)[1]
        return make_clinic_site(domain=domain), None, dict(DETAILS, city=city)

    monkeypatch.setattr(clinics_agent, "visit", fake_visit)

    candidates = [
        ("https://good.com", "q1", "serper", "Austin"),
        ("https://good.com", "q2", "brave", "Austin"),      # same domain, second provider
        ("https://www.yelp.com/biz/x", "q3", "serper", "Austin"),  # directory
        ("https://known.com", "q4", "serper", "Austin"),    # already in the CRM
        ("https://other.com", "q5", "serper", "Miami"),
    ]
    stats = {"added": 0, "duplicate": 0, "fetch_failed": 0, "skipped_aggregator": 0, "sites_visited": 0}
    seen = {domain for (domain,) in session.query(Lead.domain).all()}

    clinics_agent._process(iter(candidates), session, [], seen, stats,
                           remaining=None, workers=2, allow_non_us=False, dry_run=False)

    assert sorted(visited) == ["https://good.com", "https://other.com"]
    assert stats["added"] == 2
    assert stats["skipped_aggregator"] == 1
    assert session.query(Lead).filter(Lead.domain == "yelp.com").count() == 0


def test_process_writes_nothing_on_a_dry_run(monkeypatch):
    from clinics import agent as clinics_agent

    session = make_session()
    monkeypatch.setattr(
        clinics_agent, "visit",
        lambda url, keywords, city: (make_clinic_site(domain="dry.com"), None, DETAILS),
    )
    stats = {"added": 0, "sites_visited": 0, "fetch_failed": 0, "skipped_aggregator": 0}
    clinics_agent._process(iter([("https://dry.com", "q", "serper", "Austin")]), session, [],
                           set(), stats, remaining=None, workers=1, allow_non_us=False, dry_run=True)

    assert stats["sites_visited"] == 1
    assert session.query(Lead).count() == 0
