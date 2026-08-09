from scraper.import_list import extract_domains, merge_into_seed_file


def test_extract_domains_from_links_and_labels():
    html = """
    <a href="https://www.acmepeptides.com/shop">Acme</a>
    <a href="https://facebook.com/acme">fb</a>
    <span>bravopeptides.com</span>
    <img src="https://cdn.shopify.com/logo.png">
    """
    assert extract_domains(html) == ["acmepeptides.com", "bravopeptides.com"]


def test_extract_domains_excludes_the_directory_itself():
    html = '<a href="https://peptidebase.io/x">home</a><a href="https://acmepeptides.com">Acme</a>'
    assert extract_domains(html, source_host="peptidebase.io") == ["acmepeptides.com"]


def test_extract_domains_ignores_asset_files():
    html = '<link href="https://acmepeptides.com/style.css"><a href="https://realvendor.com">v</a>'
    got = extract_domains(html)
    assert "realvendor.com" in got


def test_merge_into_seed_file_appends_only_new(tmp_path):
    seed = tmp_path / "seeds.txt"
    seed.write_text("# comment\nhttps://acmepeptides.com/\n")
    added, already = merge_into_seed_file(["acmepeptides.com", "newvendor.com"], seed)
    assert added == ["newvendor.com"]
    assert already == 1
    assert "https://newvendor.com/" in seed.read_text()
    assert seed.read_text().count("acmepeptides.com") == 1


def test_merge_into_seed_file_creates_missing_file(tmp_path):
    seed = tmp_path / "new.txt"
    added, _ = merge_into_seed_file(["vendor.com"], seed)
    assert added == ["vendor.com"]
    assert seed.read_text().strip() == "https://vendor.com/"
