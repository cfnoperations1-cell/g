"""Search-API based discovery of candidate company websites.

Uses real search-engine APIs (not scraping search-result pages, which
violates most search engines' terms of service) to find company sites
matching a query. Each provider is optional and simply skips itself if
its API key isn't configured.
"""
from __future__ import annotations

import logging
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
