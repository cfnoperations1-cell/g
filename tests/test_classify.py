from scraper.classify import classify_company_type, contains_any_keyword, guess_us_presence

KEYWORDS = ["BPC-157", "NAD+"]


def test_contains_any_keyword_true():
    assert contains_any_keyword("We sell BPC-157 for research.", KEYWORDS)


def test_contains_any_keyword_false():
    assert not contains_any_keyword("We sell vitamins and supplements.", KEYWORDS)


def test_guess_us_presence_from_usa_mention():
    is_us, state = guess_us_presence("Proudly made in the USA.")
    assert is_us is True


def test_guess_us_presence_from_state_name():
    is_us, state = guess_us_presence("Located in Austin, Texas, we ship nationwide.")
    assert is_us is True
    assert state == "Texas"


def test_guess_us_presence_abbreviation_needs_nearby_zip():
    # "IN" near a zip code should count as Indiana, not the word "in".
    is_us, state = guess_us_presence("Visit us at 123 Main St, Springfield, IN 46201.")
    assert is_us is True
    assert state == "IN"


def test_guess_us_presence_false_for_unrelated_text():
    is_us, state = guess_us_presence("We are a research company based in Berlin, Germany.")
    assert is_us is False
    assert state is None


def test_classify_compounding_pharmacy():
    text = "Our licensed compounding pharmacy prepares custom prescriptions."
    assert classify_company_type(text, research_only_evidence=None) == "compounding_pharmacy"


def test_classify_manufacturing_lab():
    text = "Our cGMP facility specializes in custom peptide synthesis at scale."
    assert classify_company_type(text, research_only_evidence=None) == "manufacturing_lab"


def test_classify_research_only():
    text = "All peptides sold here are for laboratory use."
    assert classify_company_type(text, research_only_evidence="for research use only") == "research_only"


def test_classify_defaults_to_consumer_and_research():
    text = "Shop our best-selling peptides, ships same day."
    assert classify_company_type(text, research_only_evidence=None) == "consumer_and_research"
