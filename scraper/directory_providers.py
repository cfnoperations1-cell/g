"""Business-directory API based discovery of candidate companies.

Complements the search-API providers with a structured directory lookup.
Google Places returns the business's own website plus phone and address,
which makes it the best source for location-bound targets (med spas and
clinics in a given city); for national vendors a web search does better.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

import requests

import config

logger = logging.getLogger(__name__)

_CITY_STATE_ZIP_RE = re.compile(r",\s*([^,]+?),\s*([A-Z]{2})\s+(\d{5})")


def parse_city_state_zip(address: Optional[str]) -> Tuple[str, str, str]:
    """"123 Main St, Las Vegas, NV 89101, USA" -> ("Las Vegas", "NV", "89101")."""
    m = _CITY_STATE_ZIP_RE.search(address or "")
    return (m.group(1).strip(), m.group(2), m.group(3)) if m else ("", "", "")


@dataclass
class DirectoryResult:
    name: str
    website: str
    address: Optional[str]
    query: str
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    maps_url: Optional[str] = None
    category: Optional[str] = None

    def as_extra(self) -> dict:
        """Fields worth carrying onto a Lead when the site itself lacks them."""
        return {
            "company_name": self.name or None,
            "phone": self.phone,
            "state": self.state,
            "address": self.address,
        }


class GooglePlacesProvider:
    """Google Places API (New) text search. Requires GOOGLE_PLACES_API_KEY
    with "Places API (New)" enabled on the project.

    One request returns the website, phone and address together (the legacy
    API needed a Details call per place), and a field mask keeps each call
    on the cheaper SKU. Pagination is by page token, up to 60 results.
    """

    name = "google_places"
    ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
    FIELD_MASK = ",".join([
        "places.displayName", "places.formattedAddress", "places.websiteUri",
        "places.nationalPhoneNumber", "places.rating", "places.userRatingCount",
        "places.googleMapsUri", "places.primaryTypeDisplayName", "nextPageToken",
    ])
    PAGE_SIZE = 20

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = config.GOOGLE_PLACES_API_KEY if api_key is None else api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, num_results: int = 20) -> Iterator[DirectoryResult]:
        if not self.is_configured():
            logger.info("Google Places not configured; skipping query %r", query)
            return

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": self.FIELD_MASK,
        }
        fetched = 0
        page_token: Optional[str] = None

        while fetched < num_results:
            body = {"textQuery": query, "pageSize": min(self.PAGE_SIZE, num_results - fetched), "regionCode": "US"}
            if page_token:
                body["pageToken"] = page_token
            resp = requests.post(self.ENDPOINT, json=body, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()

            for place in data.get("places", []):
                if fetched >= num_results:
                    break
                website = place.get("websiteUri")
                if not website:
                    continue
                address = place.get("formattedAddress")
                city, state, zip_code = parse_city_state_zip(address)
                yield DirectoryResult(
                    name=(place.get("displayName") or {}).get("text", ""),
                    website=website,
                    address=address,
                    query=query,
                    phone=place.get("nationalPhoneNumber"),
                    city=city or None,
                    state=state or None,
                    zip_code=zip_code or None,
                    rating=place.get("rating"),
                    review_count=place.get("userRatingCount"),
                    maps_url=place.get("googleMapsUri"),
                    category=(place.get("primaryTypeDisplayName") or {}).get("text"),
                )
                fetched += 1

            page_token = data.get("nextPageToken")
            if not page_token:
                break
            time.sleep(2)  # tokens take a moment to become valid
