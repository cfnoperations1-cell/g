from scraper.directory_providers import GooglePlacesProvider, parse_city_state_zip


def test_parse_city_state_zip():
    assert parse_city_state_zip("123 Main St, Las Vegas, NV 89101, USA") == ("Las Vegas", "NV", "89101")
    assert parse_city_state_zip("Somewhere in Europe") == ("", "", "")
    assert parse_city_state_zip(None) == ("", "", "")


def test_places_unconfigured_yields_nothing():
    provider = GooglePlacesProvider(api_key="")
    assert provider.is_configured() is False
    assert list(provider.search("med spa peptides Las Vegas, NV", 5)) == []


def test_places_new_text_search_parses_places_and_skips_ones_without_a_site(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "places": [
                    {"displayName": {"text": "Glow Med Spa"}, "formattedAddress": "1 Strip Blvd, Las Vegas, NV 89109, USA",
                     "websiteUri": "https://glowmedspa.com/", "nationalPhoneNumber": "(702) 555-0123",
                     "rating": 4.8, "userRatingCount": 120, "googleMapsUri": "https://maps.google.com/?cid=1",
                     "primaryTypeDisplayName": {"text": "Medical spa"}},
                    {"displayName": {"text": "No Site Spa"}, "formattedAddress": "2 Elm St, Reno, NV 89501, USA"},
                ]
            }

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("scraper.directory_providers.requests.post", fake_post)
    results = list(GooglePlacesProvider(api_key="fake").search("med spa peptides Las Vegas, NV", 20))

    assert captured["url"].startswith("https://places.googleapis.com/v1/places:searchText")
    assert captured["json"]["textQuery"] == "med spa peptides Las Vegas, NV"
    assert "places.websiteUri" in captured["headers"]["X-Goog-FieldMask"]
    assert len(results) == 1
    r = results[0]
    assert r.name == "Glow Med Spa"
    assert r.website == "https://glowmedspa.com/"
    assert (r.city, r.state, r.zip_code) == ("Las Vegas", "NV", "89109")
    assert r.phone == "(702) 555-0123"
    assert r.category == "Medical spa"
    assert r.as_extra() == {"company_name": "Glow Med Spa", "phone": "(702) 555-0123", "state": "NV",
                            "address": "1 Strip Blvd, Las Vegas, NV 89109, USA"}
