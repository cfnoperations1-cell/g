from scraper.site_parser import _extract_email, find_research_only_evidence, get_domain


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
    html = "Contact privacy@example.com or sales@example.com for questions."
    assert _extract_email(html) == "sales@example.com"


def test_extract_email_ignores_generic_only():
    html = "Reach out to webmaster@example.com"
    assert _extract_email(html) is None


def test_extract_email_no_matches():
    assert _extract_email("No contact info here.") is None
