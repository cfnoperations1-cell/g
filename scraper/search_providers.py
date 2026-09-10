"""Search-API based discovery of candidate company websites.

Uses real search-engine APIs (not scraping search-result pages, which
violates most search engines' terms of service) to find company sites
matching a query. Each provider is optional and simply skips itself if
its API key isn't configured.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

import config

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    query: str


class GoogleCustomSearchProvider:
    """Google Programmable Search Engine (Custom Search JSON API).

    Requires GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX. Create a search engine at
    https://programmablesearchengine.google.com/ (set it to search the whole
    web) and an API key at https://console.cloud.google.com/.
    """

    name = "google_cse"
    ENDPOINT = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: Optional[str] = None, cx: Optional[str] = None):
        self.api_key = config.GOOGLE_CSE_API_KEY if api_key is None else api_key
        self.cx = config.GOOGLE_CSE_CX if cx is None else cx

    def is_configured(self) -> bool:
        return bool(self.api_key and self.cx)

    def search(self, query: str, num_results: int = 10) -> Iterator[SearchResult]:
        if not self.is_configured():
            logger.info("Google CSE not configured; skipping query %r", query)
            return
        fetched = 0
        start = 1
        while fetched < num_results:
            page_size = min(10, num_results - fetched)
            params = {
                "key": self.api_key,
                "cx": self.cx,
                "q": query,
                "num": page_size,
                "start": start,
                "cr": "countryUS",
                "gl": "us",
            }
            resp = requests.get(self.ENDPOINT, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                yield SearchResult(
                    url=item.get("link", ""),
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    query=query,
                )
            fetched += len(items)
            start += len(items)


class BingSearchProvider:
    """Bing Web Search API (Azure Cognitive Services). Requires BING_SEARCH_API_KEY."""

    name = "bing"
    ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = config.BING_SEARCH_API_KEY if api_key is None else api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, num_results: int = 10) -> Iterator[SearchResult]:
        if not self.is_configured():
            logger.info("Bing Search not configured; skipping query %r", query)
            return
        headers = {"Ocp-Apim-Subscription-Key": self.api_key}
        params = {"q": query, "count": min(num_results, 50), "mkt": "en-US", "cc": "US"}
        resp = requests.get(self.ENDPOINT, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("webPages", {}).get("value", []):
            yield SearchResult(
                url=item.get("url", ""),
                title=item.get("name", ""),
                snippet=item.get("snippet", ""),
                query=query,
            )


class SerperProvider:
    """Serper.dev -- Google results via a single API key, no Google Cloud
    project or billing setup required. Free tier includes a batch of
    credits; get a key at https://serper.dev/.
    """

    name = "serper"
    ENDPOINT = "https://google.serper.dev/search"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = config.SERPER_API_KEY if api_key is None else api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, num_results: int = 10) -> Iterator[SearchResult]:
        if not self.is_configured():
            logger.info("Serper not configured; skipping query %r", query)
            return
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"q": query, "num": min(num_results, 100), "gl": "us", "hl": "en"}
        resp = requests.post(self.ENDPOINT, headers=headers, json=payload, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        for item in resp.json().get("organic", []):
            yield SearchResult(
                url=item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                query=query,
            )


class BraveSearchProvider:
    """Brave Search API -- independent index, single API key, no Google
    Cloud involvement. Get a key at https://brave.com/search/api/.
    """

    name = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = config.BRAVE_SEARCH_API_KEY if api_key is None else api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, num_results: int = 10) -> Iterator[SearchResult]:
        if not self.is_configured():
            logger.info("Brave Search not configured; skipping query %r", query)
            return
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        params = {"q": query, "count": min(num_results, 20), "country": "us"}
        resp = requests.get(self.ENDPOINT, headers=headers, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        for item in resp.json().get("web", {}).get("results", []):
            yield SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("description", ""),
                query=query,
            )


def _ddgs_text(query: str, num_results: int, backend: str, timeout: int) -> list:
    """Thin wrapper around the ddgs package so tests can stub it."""
    from ddgs import DDGS  # imported lazily: optional dependency

    with DDGS(timeout=timeout) as ddgs:
        return list(ddgs.text(query, max_results=num_results, region="us-en", backend=backend))


class DuckDuckGoProvider:
    """Keyless fallback through the `ddgs` package (DuckDuckGo and the
    engines it can front). No account or key, but it is slower, rate-limited
    and noisier than a search API, so it is only used when no keyed provider
    is configured. A query that keeps failing is skipped rather than aborting
    the run -- with this backend that is a transient condition, not a
    misconfiguration.
    """

    name = "duckduckgo"

    def __init__(self, enabled: Optional[bool] = None, backend: Optional[str] = None,
                 attempts: int = 3, timeout: int = 20, pause_seconds: float = 2.0):
        self.enabled = config.DUCKDUCKGO_FALLBACK if enabled is None else enabled
        self.backend = config.DUCKDUCKGO_BACKEND if backend is None else backend
        self.attempts = attempts
        self.timeout = timeout
        self.pause_seconds = pause_seconds

    def is_configured(self) -> bool:
        if not self.enabled:
            return False
        try:
            import ddgs  # noqa: F401
        except ImportError:
            return False
        return True

    def search(self, query: str, num_results: int = 10) -> Iterator[SearchResult]:
        if not self.is_configured():
            logger.info("DuckDuckGo fallback disabled or ddgs not installed; skipping query %r", query)
            return
        rows: list = []
        for attempt in range(1, self.attempts + 1):
            try:
                rows = _ddgs_text(query, num_results, self.backend, self.timeout)
                break
            except Exception as exc:  # ddgs raises its own exception types
                logger.warning("[duckduckgo] attempt %d/%d failed for %r: %s",
                               attempt, self.attempts, query, str(exc).splitlines()[0][:160])
                time.sleep(self.pause_seconds * attempt)
        for row in rows:
            url = row.get("href") or row.get("url") or ""
            if not url:
                continue
            yield SearchResult(url=url, title=row.get("title", ""), snippet=row.get("body", ""), query=query)
        time.sleep(self.pause_seconds)


def keyed_search_providers() -> list:
    """Every API-key based provider, configured or not (callers log skips)."""
    return [GoogleCustomSearchProvider(), SerperProvider(), BraveSearchProvider(), BingSearchProvider()]


def search_providers_with_fallback() -> list:
    """Keyed providers, plus DuckDuckGo only when none of them is configured."""
    providers = keyed_search_providers()
    if not any(p.is_configured() for p in providers):
        providers.append(DuckDuckGoProvider())
    return providers
