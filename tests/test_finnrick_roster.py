import csv

from scraper.finnrick_roster import finnrick_rows, location_to_country, run, website_from_entry


def test_location_to_country():
    assert location_to_country("us") == "United States"
    assert location_to_country("US Mi, Fl") == "United States"
    assert location_to_country("china") == "China"
    assert location_to_country("uk") == "United Kingdom"
    assert location_to_country("Philippines") == "Philippines"
    assert location_to_country(None) == ""


def test_website_from_entry_takes_first_vendor_site():
    assert website_from_entry({"contact_kind": "website", "contact_url": "https://alimopeptide.com/  https://t.me/+abc"}) == "https://alimopeptide.com/"
    assert website_from_entry({"contact_kind": "website", "contact_url": "https://t.me/+abc"}) == ""
    assert website_from_entry({"contact_kind": "telegram", "contact_url": "https://t.me/x"}) == ""
    assert website_from_entry({"contact_kind": "website", "contact_url": "acme.com"}) == "https://acme.com/"


def test_finnrick_rows_skips_known_and_dead_vendors_and_ranks_tested_first():
    index = [
        {"slug": "acme-peptides", "name": "Acme Peptides", "status": "Trading", "test_count": 0, "contact_url": "https://acme.com/"},
        {"slug": "known-co", "name": "Known Co", "status": "Trading", "test_count": 5, "contact_url": "https://known.com/"},
        {"slug": "other-slug", "name": "Same Name Different Slug", "status": "Trading", "test_count": 1},
        {"slug": "dead", "name": "Dead Vendor", "status": "Deactivated", "test_count": 9, "contact_url": "https://dead.com/"},
        {"slug": "tested", "name": "Tested Labs", "status": "active", "test_count": 12, "contact_url": "https://tested.io/",
         "location": "us", "latest_test_display": "1 Sep 2026"},
    ]
    existing = [
        {"Vendor": "Known Co", "Finnrick Profile": "https://www.finnrick.com/vendors/known-co"},
        {"Vendor": "same name different slug", "Finnrick Profile": ""},
    ]
    rows = finnrick_rows(index, existing, "Vendor")
    assert [r["Vendor"] for r in rows] == ["Tested Labs", "Acme Peptides"]
    tested = rows[0]
    assert tested["Country"] == "United States"
    assert tested["Listed On"] == "Finnrick"
    assert tested["Finnrick Profile"] == "https://www.finnrick.com/vendors/tested"
    assert tested["Website"] == "https://tested.io/"
    assert tested["Notes"] == "Finnrick status: active; tests: 12; latest test 1 Sep 2026"
    assert "_tests" not in tested


class _FakeClient:
    def __init__(self, entries):
        self.entries = entries

    def _load_index(self):
        return {e["slug"]: e for e in self.entries}


def test_run_appends_rows_and_keeps_existing_columns(tmp_path):
    src = tmp_path / "v.csv"
    src.write_text("Vendor,Website,Email\nOld Co,https://old.com/,hi@old.com\n")
    out = tmp_path / "combined.csv"
    client = _FakeClient([
        {"slug": "new-co", "name": "New Co", "status": "Trading", "test_count": 3, "contact_url": "https://new.com/"},
        {"slug": "untested", "name": "Untested", "status": "Trading", "test_count": 0, "contact_url": "https://u.com/"},
    ])
    stats = run(src, out, only_tested=True, client=client)
    assert stats == {"existing": 1, "added": 1, "finnrick_total": 2}
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert [r["Vendor"] for r in rows] == ["Old Co", "New Co"]
    assert rows[0]["Email"] == "hi@old.com"
    assert rows[1]["Website"] == "https://new.com/" and rows[1]["Listed On"] == "Finnrick"
