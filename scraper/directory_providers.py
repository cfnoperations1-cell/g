"""Business-directory API based discovery of candidate companies.

Complements the search-API providers with a structured directory lookup.
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
class DirectoryResult:
    name: str
    website: str
    address: Optional[str]
    query: str


class GooglePlacesProvider:
    """Google Places API (Text Search + Details). Requires GOOGLE_PLACES_API_KEY.

    Note: Places is location-oriented, so it works best for queries that
    include a place name or region (e.g. "research peptides supplier USA").
    """

    name = "google_places"
    TEXT_SEARCH_ENDPOINT = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    DETAILS_ENDPOINT = "https://maps.googleapis.com/maps/api/place/details/json"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = config.GOOGLE_PLACES_API_KEY if api_key is None else api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, num_results: int = 20) -> Iterator[DirectoryResult]:
        if not self.is_configured():
            logger.info("Google Places not configured; skipping query %r", query)
            return

        params = {"query": query, "key": self.api_key, "region": "us"}
        fetched = 0

        while fetched < num_results:
            resp = requests.get(self.TEXT_SEARCH_ENDPOINT, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()

            for place in data.get("results", []):
                if fetched >= num_results:
                    break
                website = self._get_website(place.get("place_id", ""))
                if not website:
                    continue
                yield DirectoryResult(
                    name=place.get("name", ""),
                    website=website,
                    address=place.get("formatted_address"),
                    query=query,
                )
                fetched += 1

            next_page_token = data.get("next_page_token")
            if not next_page_token:
                break
            # Google requires a short delay before a page token becomes valid.
            time.sleep(2)
            params = {"pagetoken": next_page_token, "key": self.api_key}

    def _get_website(self, place_id: str) -> Optional[str]:
        if not place_id:
            return None
        params = {"place_id": place_id, "fields": "website", "key": self.api_key}
        resp = requests.get(self.DETAILS_ENDPOINT, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("website")
