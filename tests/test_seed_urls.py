from scraper.agent import load_seed_urls


def test_load_seed_urls_skips_blank_lines_and_comments(tmp_path):
    seed_file = tmp_path / "seeds.txt"
    seed_file.write_text(
        "# comment\n"
        "https://example.com/\n"
        "\n"
        "https://another-example.com/\n"
    )
    assert load_seed_urls(seed_file) == ["https://example.com/", "https://another-example.com/"]


def test_load_seed_urls_missing_file_returns_empty(tmp_path):
    assert load_seed_urls(tmp_path / "does-not-exist.txt") == []
