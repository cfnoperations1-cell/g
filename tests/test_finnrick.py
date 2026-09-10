import json

from scraper.finnrick import FinnrickClient, is_vendor_site, parse_contacts, slug_from_profile_url


def test_slug_from_profile_url():
    assert slug_from_profile_url("https://www.finnrick.com/vendors/aavant-research") == "aavant-research"
    assert slug_from_profile_url("https://peptidebase.io/research-vendors/x") is None
    assert slug_from_profile_url("") is None


def test_is_vendor_site_rejects_forums_and_directories():
    assert is_vendor_site("https://aavantpeptides.com/")
    assert not is_vendor_site("https://glp1forum.com/threads/amino-lair.3775/")
    assert not is_vendor_site("https://www.reddit.com/r/peptides")


def test_parse_contacts_prefers_vouched_website_and_reads_every_kind():
    vendor = {
        "slug": "aavant-research", "name": "Aavant Research", "location": None, "status": "Trading", "test_count": 21,
        "contacts": [
            {"kind": "website", "url": "https://aavant.net/", "value": "aavant.net", "vouched": False},
            {"kind": "website", "url": "https://aavantpeptides.com/", "value": "aavantpeptides.com", "vouched": True},
            {"kind": "email", "url": "mailto:aavant@peptide.email", "value": "aavant@peptide.email", "vouched": True},
            {"kind": "whatsapp", "url": "https://wa.me/18335250785", "value": "+18335250785", "vouched": False},
            {"kind": "telegram", "url": "https://t.me/aavant", "value": "@aavant", "vouched": False},
            {"kind": "signal", "url": "", "value": "aavant.01", "vouched": False},
            {"kind": "discord", "url": "https://discord.gg/abc", "value": "discord.gg/abc", "vouched": False},
        ],
    }
    c = parse_contacts(vendor)
    assert c.website == "https://aavantpeptides.com/"
    assert c.websites == ["https://aavant.net/", "https://aavantpeptides.com/"]
    assert c.emails == ["aavant@peptide.email"]
    assert c.whatsapp == ["+18335250785"]
    assert c.telegram == ["@aavant"]
    assert c.signal == ["aavant.01"]
    assert c.other == ["discord: discord.gg/abc"]
    assert c.found_anything


def test_parse_contacts_folds_in_headline_contact_and_skips_forum_links():
    vendor = {
        "slug": "amino-lair", "name": "Amino Lair",
        "contacts": [{"kind": "website", "url": "https://glp1forum.com/threads/amino-lair.3775/", "value": "glp1forum.com"}],
        "contact_url": "https://aminolair.com/", "contact_kind": "website",
    }
    c = parse_contacts(vendor)
    assert c.website == "https://aminolair.com/"
    assert c.websites == ["https://aminolair.com/"]


def test_parse_contacts_empty_vendor():
    c = parse_contacts({"slug": "ghost", "name": "Ghost", "contacts": [], "contact_url": None})
    assert not c.found_anything
    assert c.website is None


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_client_looks_up_by_slug_or_name_and_caches_index(tmp_path, monkeypatch):
    index = {"items": [
        {"slug": "alpha-omega-peptide", "name": "Alpha Omega Peptide", "contacts": [
            {"kind": "website", "url": "https://alphaomegapeptide.com/", "value": "alphaomegapeptide.com", "vouched": True}]},
        {"slug": "ghost-labs", "name": "Ghost Labs", "contacts": [], "contact_url": "https://ghostlabs.io/",
         "contact_kind": "website", "has_report": True},
    ]}
    # The real index has an empty contacts list on every entry and only a
    # headline website; the detail record carries the rest.
    detail = {"vendor": {"slug": "ghost-labs", "name": "Ghost Labs", "contacts": [
        {"kind": "email", "url": "mailto:hi@ghostlabs.io", "value": "hi@ghostlabs.io"}]}}
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/api/v1/vendors"):
            return _Resp(index)
        if url.endswith("/api/v1/vendors/ghost-labs"):
            return _Resp(detail)
        return _Resp({"detail": "Not found"}, status=404)

    monkeypatch.setattr("scraper.finnrick.requests.get", fake_get)
    client = FinnrickClient(cache_file=tmp_path / "idx.json")

    by_name = client.lookup(name="alpha omega peptide")
    assert by_name is not None and by_name.website == "https://alphaomegapeptide.com/"
    by_slug = client.lookup(slug="alpha-omega-peptide")
    assert by_slug is not None and by_slug.slug == "alpha-omega-peptide"
    assert client.lookup(name="Nobody Here") is None

    # An index entry with no contacts triggers exactly one detail fetch.
    ghost = client.lookup(slug="ghost-labs")
    assert ghost is not None and ghost.emails == ["hi@ghostlabs.io"]
    assert ghost.website == "https://ghostlabs.io/"   # headline site kept when detail lacks one
    assert calls.count("https://www.finnrick.com/api/v1/vendors") == 1
    assert calls.count("https://www.finnrick.com/api/v1/vendors/ghost-labs") == 1

    # The index was written to disk, and a fresh client reads it back without a request.
    assert json.loads((tmp_path / "idx.json").read_text())["items"]
    client2 = FinnrickClient(cache_file=tmp_path / "idx.json")
    assert client2.lookup(slug="alpha-omega-peptide") is not None
    assert calls.count("https://www.finnrick.com/api/v1/vendors") == 1
