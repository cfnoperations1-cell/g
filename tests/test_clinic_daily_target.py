"""End-to-end check of the daily-quota loop with the network stubbed out.

The point of the clinics agent is "N new leads by end of day", so the thing
worth testing is that run() actually converges on the target, stops once it
gets there, and leaves a cursor behind so tomorrow starts somewhere new.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from clinics import agent as clinics_agent
from db import Base
from models import DiscoveryState, Lead, LeadKind
from scraper.search_providers import SearchResult
from scraper.site_parser import SiteData

CLINIC_PAGE = (
    "Peak Wellness Clinic of Austin, Texas 78701. Our providers offer peptide therapy, "
    "BPC-157 and Sermorelin. Book an appointment with our medical director. "
    "New patients welcome. Call or email info@peak.com."
)


class FakeSearchProvider:
    """Returns a fresh domain for every result, so discovery never stalls."""

    name = "fake"

    def __init__(self):
        self.queries = []

    def is_configured(self):
        return True

    def search(self, query, num_results=10):
        self.queries.append(query)
        index = len(self.queries)
        for n in range(num_results):
            yield SearchResult(
                url=f"https://clinic-{index}-{n}.com",
                title="Clinic",
                snippet="",
                query=query,
            )


def install_fakes(monkeypatch, tmp_path, page_text=CLINIC_PAGE):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr(clinics_agent, "SessionLocal", Session)
    monkeypatch.setattr(clinics_agent, "init_db", lambda: None)

    provider = FakeSearchProvider()
    monkeypatch.setattr(clinics_agent, "configured_search_providers", lambda: [provider])

    def fake_parse_site(url, peptide_keywords=None, candidate_paths=None):
        domain = url.split("//", 1)[1]
        return SiteData(
            url=url, domain=domain, company_name=f"Clinic {domain}",
            email=f"info@{domain}", us_based=True, state="Texas",
            pages_checked=[url], full_text=page_text,
        )

    monkeypatch.setattr(clinics_agent, "parse_site", fake_parse_site)
    return Session, provider


def test_run_reaches_the_daily_target_and_stops(monkeypatch, tmp_path):
    Session, provider = install_fakes(monkeypatch, tmp_path)

    stats = clinics_agent.run(
        daily_target=15, per_query=5, max_queries=200, query_batch=5,
        workers=4, use_directory=False,
    )

    assert stats["added"] >= 15
    session = Session()
    assert session.query(Lead).filter(Lead.kind == LeadKind.CLINIC.value).count() >= 15
    # It stopped near the target rather than spending the whole query budget.
    assert stats["queries_run"] < 200


def test_a_second_run_the_same_day_only_tops_up(monkeypatch, tmp_path):
    Session, _ = install_fakes(monkeypatch, tmp_path)

    clinics_agent.run(daily_target=10, per_query=5, max_queries=100, query_batch=5,
                      workers=4, use_directory=False)
    first_total = Session().query(Lead).count()

    second = clinics_agent.run(daily_target=10, per_query=5, max_queries=100, query_batch=5,
                               workers=4, use_directory=False)
    assert second["added"] == 0
    assert Session().query(Lead).count() == first_total


def test_the_cursor_advances_so_tomorrow_searches_somewhere_new(monkeypatch, tmp_path):
    Session, provider = install_fakes(monkeypatch, tmp_path)

    clinics_agent.run(daily_target=5, per_query=5, max_queries=50, query_batch=5,
                      workers=4, use_directory=False)
    cursor = Session().get(DiscoveryState, clinics_agent.AGENT_NAME).query_cursor
    assert cursor > 0

    first_day_queries = set(provider.queries)
    # A second day: the target is met for today, so force a fresh run by
    # asking for no target at all and a small budget.
    clinics_agent.run(daily_target=None, per_query=5, max_queries=5, query_batch=5,
                      workers=4, use_directory=False)
    second_day_queries = set(provider.queries) - first_day_queries
    assert second_day_queries
    assert not (second_day_queries & first_day_queries)


def test_sites_that_are_not_clinics_are_not_saved(monkeypatch, tmp_path):
    Session, _ = install_fakes(
        monkeypatch, tmp_path,
        page_text="A blog post about peptide therapy trends. Read our buyer's guide.",
    )

    stats = clinics_agent.run(daily_target=5, per_query=5, max_queries=10, query_batch=5,
                              workers=4, use_directory=False)

    assert stats["added"] == 0
    assert stats["skipped_not_a_clinic"] > 0
    assert Session().query(Lead).count() == 0
