from scraper.classify import (
    classify_company_type,
    contains_any_keyword,
    guess_us_presence,
    is_content_site,
    manufactures,
    sells_direct,
)

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


def test_classify_ignores_negated_compounding_disclaimer():
    # Real disclaimer text seen in the wild: a supplier explicitly denying
    # being a compounding pharmacy shouldn't be classified as one.
    text = "We are a chemical supplier. We are not a compounding pharmacy or chemical compounding facility."
    assert classify_company_type(text, research_only_evidence="for research use only") == "research_only"


def test_classify_ignores_negated_manufacturing_disclaimer():
    text = "This site is not a gmp facility and does not manufacture peptides."
    assert classify_company_type(text, research_only_evidence=None) == "consumer_and_research"


def test_sells_direct_detects_storefront():
    assert sells_direct("Add to cart. In stock. Free shipping on orders over $100.")


def test_sells_direct_false_for_info_page():
    assert not sells_direct("This article explains what BPC-157 is and how it works.")


def test_manufactures_detects_own_synthesis():
    assert manufactures("All peptides are synthesized in our cGMP facility.")


def test_manufactures_false_when_negated():
    assert not manufactures("We are not a gmp facility and do not manufacture.")


def test_is_content_site_detects_affiliate_review():
    text = "We may earn a commission. Our editorial team fact-checked this buying guide."
    assert is_content_site(text)


def test_is_content_site_false_for_store():
    assert not is_content_site("Add to cart. Shop our peptides. In stock now.")


def test_classify_med_spa_from_practice_copy():
    from scraper.classify import looks_like_med_spa

    text = "Glow Med Spa offers Botox, dermal fillers, microneedling and peptide therapy. Book your facial today."
    assert looks_like_med_spa(text)
    assert classify_company_type(text, research_only_evidence=None) == "med_spa"


def test_classify_clinic_from_practice_copy():
    from scraper.classify import looks_like_clinic

    text = "Our longevity clinic provides hormone therapy, IV therapy and peptide protocols. Book a consultation with our providers."
    assert looks_like_clinic(text)
    assert classify_company_type(text, research_only_evidence=None) == "clinic"


def test_storefront_with_spa_words_is_still_a_seller():
    text = "Add to cart. In stock. Free shipping. Our aesthetic peptides are loved by med spa clients and facial specialists."
    assert classify_company_type(text, research_only_evidence=None) == "consumer_and_research"


def test_single_spa_word_is_not_enough():
    from scraper.classify import looks_like_med_spa

    assert not looks_like_med_spa("We ship research peptides to labs and the occasional med spa.")
