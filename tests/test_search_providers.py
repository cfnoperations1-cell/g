from scraper.search_providers import BraveSearchProvider, SerperProvider


def test_serper_unconfigured_yields_nothing():
    provider = SerperProvider(api_key="")
    assert provider.is_configured() is False
    assert list(provider.search("peptides", 5)) == []


def test_brave_unconfigured_yields_nothing():
    provider = BraveSearchProvider(api_key="")
    assert provider.is_configured() is False
    assert list(provider.search("peptides", 5)) == []


def test_serper_parses_organic_results(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "organic": [
                    {"link": "https://acmepeptides.com", "title": "Acme", "snippet": "research peptides"},
                ]
            }

    monkeypatch.setattr("scraper.search_providers.requests.post", lambda *a, **kw: FakeResponse())
    results = list(SerperProvider(api_key="fake").search("peptides", 5))
    assert len(results) == 1
    assert results[0].url == "https://acmepeptides.com"
    assert results[0].query == "peptides"


def test_brave_parses_web_results(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "web": {
                    "results": [
                        {"url": "https://acmepeptides.com", "title": "Acme", "description": "research peptides"},
                    ]
                }
            }

    monkeypatch.setattr("scraper.search_providers.requests.get", lambda *a, **kw: FakeResponse())
    results = list(BraveSearchProvider(api_key="fake").search("peptides", 5))
    assert len(results) == 1
    assert results[0].url == "https://acmepeptides.com"
    assert results[0].snippet == "research peptides"


def test_failing_provider_does_not_abort_the_run(monkeypatch):
    """A provider erroring (bad key, API disabled, quota) must not stop the
    other providers from running."""
    import requests

    from scraper import agent

    class BoomProvider:
        name = "boom"

        def is_configured(self):
            return True

        def search(self, query, num_results):
            raise requests.HTTPError("403 Forbidden")
            yield  # pragma: no cover - generator marker

    class WorkingProvider:
        name = "working"

        def is_configured(self):
            return True

        def search(self, query, num_results):
            from scraper.search_providers import SearchResult

            yield SearchResult(url="https://acmepeptides.com", title="Acme", snippet="", query=query)

    monkeypatch.setattr(
        agent, "GoogleCustomSearchProvider", lambda *a, **kw: BoomProvider()
    )
    monkeypatch.setattr(agent, "SerperProvider", lambda *a, **kw: WorkingProvider())
    monkeypatch.setattr(agent, "BraveSearchProvider", lambda *a, **kw: BoomProvider())
    monkeypatch.setattr(agent, "BingSearchProvider", lambda *a, **kw: BoomProvider())

    results = list(agent.candidate_urls_from_search(["peptides"], 5))
    assert results == [("https://acmepeptides.com", "peptides", "working")]


def test_duckduckgo_disabled_is_not_configured():
    from scraper.search_providers import DuckDuckGoProvider

    provider = DuckDuckGoProvider(enabled=False)
    assert provider.is_configured() is False
    assert list(provider.search("peptides", 5)) == []


def test_duckduckgo_parses_rows_from_ddgs(monkeypatch):
    from scraper.search_providers import DuckDuckGoProvider

    monkeypatch.setattr("scraper.search_providers._ddgs_text", lambda q, n, backend, timeout: [
        {"href": "https://acmepeptides.com/", "title": "Acme", "body": "research peptides"},
        {"title": "no link"},
    ])
    monkeypatch.setattr("scraper.search_providers.time.sleep", lambda s: None)
    provider = DuckDuckGoProvider(enabled=True)
    monkeypatch.setattr(provider, "is_configured", lambda: True)
    results = list(provider.search("peptides", 5))
    assert [r.url for r in results] == ["https://acmepeptides.com/"]
    assert results[0].snippet == "research peptides"


def test_duckduckgo_transient_failure_yields_nothing_without_raising(monkeypatch):
    from scraper.search_providers import DuckDuckGoProvider

    def boom(q, n, backend, timeout):
        raise RuntimeError("Connection reset by peer")

    monkeypatch.setattr("scraper.search_providers._ddgs_text", boom)
    monkeypatch.setattr("scraper.search_providers.time.sleep", lambda s: None)
    provider = DuckDuckGoProvider(enabled=True, attempts=2)
    monkeypatch.setattr(provider, "is_configured", lambda: True)
    assert list(provider.search("peptides", 5)) == []


def test_agent_falls_back_to_duckduckgo_only_when_nothing_is_keyed(monkeypatch):
    from scraper import agent
    from scraper.search_providers import SearchResult

    class Unconfigured:
        name = "unconfigured"

        def is_configured(self):
            return False

        def search(self, query, num_results):
            return iter(())

    class FakeDDG:
        name = "duckduckgo"

        def is_configured(self):
            return True

        def search(self, query, num_results):
            yield SearchResult(url="https://acmepeptides.com", title="Acme", snippet="", query=query)

    for name in ("GoogleCustomSearchProvider", "SerperProvider", "BraveSearchProvider", "BingSearchProvider"):
        monkeypatch.setattr(agent, name, lambda *a, **kw: Unconfigured())
    monkeypatch.setattr(agent, "DuckDuckGoProvider", lambda *a, **kw: FakeDDG())

    assert list(agent.candidate_urls_from_search(["peptides"], 5)) == [("https://acmepeptides.com", "peptides", "duckduckgo")]
