from scraper.site_parser import (
    _extract_email,
    find_research_only_evidence,
    get_domain,
    is_usable_email,
    pick_best_email,
)


def test_get_domain_strips_www():
    assert get_domain("https://www.example.com/about") == "example.com"


def test_get_domain_keeps_other_subdomains():
    assert get_domain("https://shop.example.com") == "shop.example.com"


def test_find_research_only_evidence_found():
    text = "All products are sold strictly For Research Use Only. Not for human consumption."
    evidence = find_research_only_evidence(text)
    assert evidence is not None
    assert "research use only" in evidence.lower()


def test_find_research_only_evidence_missing():
    text = "We sell premium supplements for everyday wellness."
    assert find_research_only_evidence(text) is None


def test_extract_email_prefers_sales_or_info():
    html = "Contact privacy@acmepeptides.com or sales@acmepeptides.com for questions."
    assert _extract_email(html) == "sales@acmepeptides.com"


def test_extract_email_ignores_generic_only():
    html = "Reach out to webmaster@acmepeptides.com"
    assert _extract_email(html) is None


def test_extract_email_no_matches():
    assert _extract_email("No contact info here.") is None


def test_is_usable_email_rejects_percent_encoded_template_token():
    # Seen in the wild: a mailto of "[email]" left unfilled by a template.
    assert not is_usable_email("%5Bemail%5D@example")


def test_is_usable_email_rejects_site_builder_placeholder():
    assert not is_usable_email("contact@mysite.com")


def test_is_usable_email_accepts_real_company_address():
    assert is_usable_email("support@americanpeptides.us")


def test_extract_email_skips_placeholders_for_real_address():
    html = "Email contact@mysite.com or support@realcompany.com today."
    assert _extract_email(html) == "support@realcompany.com"


def test_pick_best_email_prefers_company_domain():
    got = pick_best_email(["someone@gmail.com", "sales@acmepeptides.com"], "acmepeptides.com")
    assert got == "sales@acmepeptides.com"


def test_pick_best_email_drops_trailing_typo_variant():
    # A page containing both a valid address and a markup-mangled variant.
    got = pick_best_email(["hello@acmepeptides.come", "hello@acmepeptides.com"], "acmepeptides.com")
    assert got == "hello@acmepeptides.com"


def test_pick_best_email_strips_whitespace():
    assert pick_best_email(["  support@acmepeptides.com "], "acmepeptides.com") == "support@acmepeptides.com"


def test_pick_best_email_none_when_all_placeholders():
    assert pick_best_email(["contact@mysite.com", "noreply@acme.com"], "acme.com") is None


def test_is_usable_email_rejects_unfilled_form_placeholder():
    # A mailto of "your@email" -- not a real address, and not regex-valid.
    assert not is_usable_email("your@email")
    assert not is_usable_email("your@email.com")


def test_is_usable_email_rejects_malformed():
    assert not is_usable_email("not-an-email")
    assert not is_usable_email("a@b")


def test_pick_best_email_ranks_mailto_and_text_together():
    # The mangled address arrives first (as a mailto would); the correct one
    # must still win rather than the first entry short-circuiting.
    got = pick_best_email(["hello@acmepeptides.come", "hello@acmepeptides.com"], "acmepeptides.com")
    assert got == "hello@acmepeptides.com"


def test_is_public_host_blocks_internal_targets():
    from scraper.site_parser import is_public_host

    for host in ("localhost", "127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0"):
        assert not is_public_host(host), host


def test_is_public_host_rejects_unresolvable():
    from scraper.site_parser import is_public_host

    assert not is_public_host("definitely-not-a-real-host.invalid")


def test_parse_site_refuses_non_public_host():
    from scraper.site_parser import parse_site

    data = parse_site("http://169.254.169.254/latest/meta-data/")
    assert data.pages_checked == []
    assert data.email is None


def test_extract_instagram_skips_non_profile_paths():
    from scraper.site_parser import extract_instagram

    html = '<a href="https://www.instagram.com/p/abc123/">post</a> <a href="https://instagram.com/glow.medspa">IG</a>'
    assert extract_instagram(html) == "@glow.medspa"
    assert extract_instagram("<p>no socials</p>") is None


def test_thin_page_and_challenge_detection():
    from scraper.site_parser import _is_thin_page, _looks_like_challenge

    assert _is_thin_page('<html><body><div id="root"></div><script src="app.js"></script></body></html>')
    assert not _is_thin_page("<html><body>" + "Real peptide content. " * 30 + "</body></html>")
    assert _looks_like_challenge("<title>Just a moment...</title>")
    assert not _looks_like_challenge("<title>Acme Peptides</title>")


class _FakeResp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text
        self.headers = {}
        self.is_redirect = False
        self.is_permanent_redirect = False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")


def test_fetch_uses_browser_fallback_on_bot_challenge(monkeypatch):
    from scraper import site_parser

    rendered = "<html><body>" + "Rendered peptide storefront. " * 20 + "</body></html>"
    monkeypatch.setattr(site_parser.requests, "get", lambda *a, **kw: _FakeResp(403))
    monkeypatch.setattr(site_parser, "browser_fallback_available", lambda: True)
    monkeypatch.setattr(site_parser, "_browser_fetch", lambda url: rendered)
    assert site_parser._fetch("https://acmepeptides.com/") == rendered


def test_fetch_without_browser_returns_none_on_challenge(monkeypatch):
    from scraper import site_parser

    monkeypatch.setattr(site_parser.requests, "get", lambda *a, **kw: _FakeResp(200, "<title>Just a moment...</title>"))
    monkeypatch.setattr(site_parser, "browser_fallback_available", lambda: False)
    assert site_parser._fetch("https://acmepeptides.com/") is None


def test_fetch_keeps_plain_html_when_browser_not_needed(monkeypatch):
    from scraper import site_parser

    html = "<html><body>" + "Plain peptide storefront copy. " * 20 + "</body></html>"
    monkeypatch.setattr(site_parser.requests, "get", lambda *a, **kw: _FakeResp(200, html))
    monkeypatch.setattr(site_parser, "_browser_fetch", lambda url: (_ for _ in ()).throw(AssertionError("should not render")))
    assert site_parser._fetch("https://acmepeptides.com/") == html


def _robots_resp(status, text=""):
    r = _FakeResp(status, text)
    return r


def test_robots_fetched_with_bot_user_agent_and_parsed(monkeypatch):
    from scraper import site_parser

    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["ua"] = headers["User-Agent"]
        return _robots_resp(200, "User-agent: *\nDisallow: /wp-admin/\n")

    monkeypatch.setattr(site_parser.requests, "get", fake_get)
    monkeypatch.setattr(site_parser, "_robots_cache", {})
    assert site_parser._allowed_by_robots("acmepeptides.com", "/")
    assert not site_parser._allowed_by_robots("acmepeptides.com", "/wp-admin/x")
    assert seen["ua"] == site_parser.config.SCRAPER_USER_AGENT


def test_robots_4xx_means_no_restrictions_and_5xx_means_stay_out(monkeypatch):
    from scraper import site_parser

    monkeypatch.setattr(site_parser.requests, "get", lambda url, headers=None, timeout=None: _robots_resp(403))
    monkeypatch.setattr(site_parser, "_robots_cache", {})
    assert site_parser._allowed_by_robots("blocked-robots.com", "/")

    monkeypatch.setattr(site_parser.requests, "get", lambda url, headers=None, timeout=None: _robots_resp(503))
    monkeypatch.setattr(site_parser, "_robots_cache", {})
    assert not site_parser._allowed_by_robots("down.com", "/")
