from clinics.classify import (
    classify_clinic_type,
    detect_peptides,
    evaluate,
    find_peptide_evidence,
    is_aggregator_domain,
    is_clinic,
    is_research_vendor,
    is_telehealth,
    mentions_city,
)

KEYWORDS = ["BPC-157", "Semaglutide", "Sermorelin", "NAD+"]

CLINIC_TEXT = (
    "Vitality Wellness Clinic of Austin, Texas. Our providers offer peptide therapy, "
    "hormone replacement therapy and medical weight loss. Book an appointment with our "
    "medical director today. New patients welcome."
)


def test_is_clinic_needs_two_signals():
    assert is_clinic(CLINIC_TEXT)


def test_is_clinic_false_for_a_single_stray_mention():
    assert not is_clinic("Ask your clinic whether this product is right for you.")


def test_find_peptide_evidence_from_therapy_phrase():
    evidence = find_peptide_evidence(CLINIC_TEXT, KEYWORDS)
    assert evidence is not None
    assert "peptide therapy" in evidence.lower()


def test_find_peptide_evidence_ignores_a_denial():
    text = "We do not offer peptide therapy at this location."
    assert find_peptide_evidence(text, []) is None


def test_bare_compound_counts_only_in_clinic_context():
    article = "Semaglutide was approved in 2021 and has been widely studied since."
    assert find_peptide_evidence(article, KEYWORDS) is None

    clinic = (
        "Our practice prescribes Semaglutide for weight management. "
        "Book an appointment with our nurse practitioner."
    )
    assert find_peptide_evidence(clinic, KEYWORDS) is not None


RESEARCH_STOREFRONT = (
    "Buy BPC-157 online at the best prices anywhere. All products are for research "
    "use only and not for human consumption. Add to cart for fast free shipping on "
    "orders over $200. In stock now, ships same day. Our research-grade purity is "
    "third-party tested with a certificate of analysis on every vial. Checkout is "
    "secure. Select options below, choose your quantity, and view your subtotal at "
    "checkout. Bulk peptides and wholesale peptides available for laboratories."
)


def test_research_vendor_is_not_a_clinic_lead():
    assert is_research_vendor(RESEARCH_STOREFRONT)
    skip_reason, _ = evaluate(RESEARCH_STOREFRONT, KEYWORDS)
    assert skip_reason == "skipped_research_vendor"


def test_a_clinic_disparaging_research_chemicals_is_still_a_clinic():
    """The commonest false positive on real sites: good clinics talk about
    research chemicals constantly, to distance themselves from them. Naming
    the phrase can't be what disqualifies a site, or the best-quality
    clinics get thrown away."""
    text = (
        "Vitality Wellness Clinic of Austin, Texas. Safe, regulated access: avoid the "
        "risks of research chemicals sold online, which are labeled not for human "
        "consumption and carry no purity guarantee, no sterility guarantee and no "
        "medical oversight. We provide a legal pathway to prescription-grade peptide "
        "therapy through a licensed compounding pharmacy. Our providers build every "
        "protocol around your labs. Book an appointment with our medical director "
        "today. New patients welcome at our clinic."
    )
    assert not is_research_vendor(text)
    skip_reason, details = evaluate(text, KEYWORDS)
    assert skip_reason is None
    assert details["clinic_type"]


def test_a_page_with_no_readable_text_is_reported_separately():
    """Client-side-rendered sites serve an empty shell. That is "couldn't
    read it", not "judged it and rejected it" -- conflating the two hid a
    chunk of real clinics in the not-a-clinic bucket."""
    skip_reason, _ = evaluate("   ", KEYWORDS)
    assert skip_reason == "skipped_no_page_text"
    skip_reason, _ = evaluate("Loading...", KEYWORDS)
    assert skip_reason == "skipped_no_page_text"


def test_detect_peptides_lists_what_the_site_names():
    assert detect_peptides(CLINIC_TEXT, KEYWORDS) == []
    text = CLINIC_TEXT + " We offer BPC-157 and NAD+ injections."
    assert detect_peptides(text, KEYWORDS) == ["BPC-157", "NAD+"]


def test_classify_hormone_clinic_beats_med_spa():
    text = (
        "We offer testosterone replacement, bioidentical hormone pellets and low T "
        "treatment, plus Botox and dermal filler for aesthetics."
    )
    assert classify_clinic_type(text) == "hormone_clinic"


def test_classify_weight_loss_clinic():
    text = "Our medical weight loss program uses GLP-1 medications like semaglutide and tirzepatide."
    assert classify_clinic_type(text) == "weight_loss_clinic"


def test_classify_regenerative_clinic():
    text = "Regenerative medicine and sports medicine: platelet-rich plasma and prp injection therapy."
    assert classify_clinic_type(text) == "regenerative_clinic"


def test_classify_falls_back_to_wellness():
    text = "A concierge practice offering peptide therapy and general consultations."
    assert classify_clinic_type(text) == "wellness_clinic"


def test_telehealth_detection():
    assert is_telehealth("Virtual consultation available; medications shipped to your door.")
    assert not is_telehealth("Visit our office at 100 Main Street.")


def test_mentions_city_is_word_bounded():
    assert mentions_city("Serving greater Austin and Round Rock", "Austin")
    # "Orlando" must not match inside another word.
    assert not mentions_city("Our Orlandoesque decor", "Orlando")


def test_aggregator_domains_including_subdomains():
    assert is_aggregator_domain("yelp.com")
    assert is_aggregator_domain("www.healthgrades.com")
    assert is_aggregator_domain("biz.yelp.com")
    assert not is_aggregator_domain("austinpeptideclinic.com")
    # A domain that merely ends with the same letters is not a subdomain.
    assert not is_aggregator_domain("notyelp.com")


def test_evaluate_accepts_a_real_clinic():
    skip_reason, details = evaluate(CLINIC_TEXT, KEYWORDS, queried_city="Austin")
    assert skip_reason is None
    assert details["clinic_type"] == "hormone_clinic"
    assert details["city"] == "Austin"
    assert details["peptide_evidence"]


def test_evaluate_rejects_a_clinic_with_no_peptides():
    text = (
        "Riverbend Family Dentistry has served the neighbourhood for thirty years. "
        "Book an appointment with our providers for cleanings, crowns, whitening and "
        "orthodontics. New patients welcome. Our medical director is board certified "
        "and our office hours are Monday through Friday. We accept most insurance "
        "plans and offer financing for larger treatment plans. Call today to schedule "
        "a consultation with our team."
    )
    skip_reason, _ = evaluate(text, KEYWORDS)
    assert skip_reason == "skipped_no_peptides"


def test_evaluate_rejects_a_non_clinic():
    text = (
        "A blog about peptide therapy trends in 2026. This article covers what the "
        "research says about growth hormone secretagogues, what the FDA has said "
        "about compounding, and where the market appears to be heading next year. "
        "Table of contents below. Read our review of the most talked-about compounds, "
        "and see our buyer's guide for what to ask before starting anything. We may "
        "earn a commission from links in this article."
    )
    skip_reason, _ = evaluate(text, KEYWORDS)
    assert skip_reason == "skipped_not_a_clinic"


def test_signals_match_on_word_boundaries():
    """Substring matching produced false positives that mattered: "your
    clinic" contains "our clinic", and "clinical trial" contains "clinic"."""
    assert not is_clinic("Ask your clinic whether this is right for you.")
    assert not is_clinic("Results from a clinical trial published last year.")
    # A real practice still reads as one.
    assert is_clinic("Our clinic is open Monday to Friday. Book an appointment online.")
