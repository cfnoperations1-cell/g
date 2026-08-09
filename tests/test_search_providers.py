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
