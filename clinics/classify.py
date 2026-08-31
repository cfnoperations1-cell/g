"""Heuristics for deciding whether a site is a US medical clinic that offers
peptides, and what kind of clinic it is.

The vendor scraper asks "does this company sell peptides?". This asks a
different question: "does this practice treat patients, and does it offer
peptides as part of that treatment?" -- which is why it has its own signal
sets rather than reusing scraper/classify.py wholesale. It does reuse that
module for the parts that are genuinely the same (US presence, negation-aware
phrase matching, storefront detection).
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from scraper.classify import _has_unnegated_signal, sells_direct  # noqa: F401  (re-exported for tests)

# A practice that treats patients: booking, providers, patient language.
CLINIC_SIGNALS = [
    "book an appointment", "book appointment", "schedule a consultation",
    "schedule an appointment", "request an appointment", "new patient",
    "new patients", "our patients", "patient portal", "our providers",
    "medical director", "nurse practitioner", "physician assistant",
    "board-certified", "board certified", "consultation", "our clinic",
    "our practice", "office hours", "telehealth", "telemedicine",
    "hipaa", "med spa", "medspa", "wellness center", "wellness clinic",
    "treatment plan", "your provider", "clinic",
]

# Phrases a clinic uses when it actually offers peptides as a treatment.
PEPTIDE_THERAPY_PHRASES = [
    "peptide therapy", "peptide therapies", "peptide treatment",
    "peptide treatments", "peptide injections", "peptide injection",
    "peptide protocol", "peptide protocols", "peptide program",
    "peptide programs", "peptide clinic", "peptides for", "peptide shots",
    "prescription peptides", "medical grade peptides", "peptide regimen",
]

# Clinic-type signals, checked in the order given by classify_clinic_type --
# most specific first, so a TRT practice that also lists facials is a hormone
# clinic rather than a med spa.
HORMONE_SIGNALS = [
    "hormone replacement therapy", "hormone optimization", "bioidentical hormone",
    "testosterone replacement", "trt", "hrt", "low testosterone", "low t",
    "estrogen therapy", "menopause treatment", "andropause", "hormone pellet",
]

REGENERATIVE_SIGNALS = [
    "regenerative medicine", "platelet-rich plasma", "platelet rich plasma",
    "prp injection", "stem cell therapy", "exosome therapy", "joint injection",
    "sports medicine", "orthobiologic", "tissue repair", "shockwave therapy",
]

WEIGHT_LOSS_SIGNALS = [
    "medical weight loss", "weight loss program", "weight loss clinic",
    "glp-1", "glp1", "semaglutide", "tirzepatide", "wegovy", "zepbound",
    "ozempic", "mounjaro", "body composition", "weight management program",
]

MED_SPA_SIGNALS = [
    "med spa", "medspa", "medical spa", "aesthetics", "aesthetic treatments",
    "botox", "dermal filler", "microneedling", "laser hair removal",
    "hydrafacial", "chemical peel", "coolsculpting", "body contouring",
]

WELLNESS_SIGNALS = [
    "functional medicine", "integrative medicine", "anti-aging",
    "antiaging", "longevity", "wellness clinic", "wellness center",
    "iv therapy", "iv hydration", "vitamin injections", "root cause",
    "concierge medicine", "biohacking",
]

TELEHEALTH_SIGNALS = [
    "telehealth", "telemedicine", "virtual consultation", "virtual visit",
    "online consultation", "see a provider online", "shipped to your door",
    "delivered to your door", "no office visit",
]

# Signals that a site is a research-chemical storefront rather than a
# practice -- those belong to the vendor scraper, not here.
RESEARCH_VENDOR_SIGNALS = [
    "for research use only", "research use only", "not for human consumption",
    "research purposes only", "not intended for human", "research chemicals",
    "bulk peptides", "wholesale peptides",
]

# Directories, marketplaces, booking platforms, and social pages. These rank
# highly for local searches but are never the clinic's own site, so a lead
# built from one would be worthless.
AGGREGATOR_DOMAINS = {
    "yelp.com", "healthgrades.com", "zocdoc.com", "vitals.com", "webmd.com",
    "wellness.com", "vagaro.com", "booksy.com", "groupon.com", "mapquest.com",
    "yellowpages.com", "bbb.org", "facebook.com", "instagram.com",
    "linkedin.com", "twitter.com", "x.com", "tiktok.com", "youtube.com",
    "pinterest.com", "reddit.com", "tripadvisor.com", "google.com",
    "bing.com", "apple.com", "amazon.com", "ebay.com", "indeed.com",
    "ziprecruiter.com", "glassdoor.com", "realself.com", "ratemds.com",
    "doximity.com", "sharecare.com", "medium.com", "wikipedia.org",
    "nih.gov", "ncbi.nlm.nih.gov", "fda.gov", "clinicaltrials.gov",
    "eventbrite.com", "meetup.com", "squarespace.com", "wixsite.com",
    "wordpress.com", "blogspot.com", "sondermind.com", "solvhealth.com",
}

_EVIDENCE_WINDOW = 60

# Signal phrases are matched on word boundaries, not as bare substrings:
# "your clinic" contains "our clinic", "shirt" contains "hrt", and "clinical
# trial" contains "clinic". Substring matching turned all three into false
# positives, which matters most for is_clinic() -- the check that decides
# whether a page is a practice at all.
_pattern_cache: dict = {}


def _signal_pattern(signal: str) -> "re.Pattern":
    pattern = _pattern_cache.get(signal)
    if pattern is None:
        left = r"\b" if signal[:1].isalnum() else ""
        right = r"\b" if signal[-1:].isalnum() else ""
        pattern = re.compile(left + re.escape(signal) + right)
        _pattern_cache[signal] = pattern
    return pattern


def is_aggregator_domain(domain: str) -> bool:
    """True for directory/social/marketplace domains (including subdomains)."""
    domain = (domain or "").lower().lstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return any(domain == blocked or domain.endswith("." + blocked) for blocked in AGGREGATOR_DOMAINS)


def _count_signals(lowered: str, signals: List[str]) -> int:
    return sum(1 for signal in signals if _signal_pattern(signal).search(lowered))


def is_clinic(text: str) -> bool:
    """True if the site reads as a practice that treats patients.

    Two independent signals are required: single words like "clinic" show up
    in plenty of pages that aren't one (a vendor's "ask your clinic" copy, a
    blog post), while a booking prompt plus provider language together are a
    reliable tell.
    """
    return _count_signals(text.lower(), CLINIC_SIGNALS) >= 2


def is_research_vendor(text: str) -> bool:
    """True for research-chemical / bulk-peptide storefronts.

    These are the vendor scraper's target, not this one's, and they use the
    same compound names -- so they have to be filtered out explicitly.
    """
    return _has_unnegated_signal(text.lower(), RESEARCH_VENDOR_SIGNALS)


def find_peptide_evidence(text: str, peptide_keywords: Optional[List[str]] = None) -> Optional[str]:
    """A snippet showing the clinic offers peptides, or None.

    A therapy phrase ("peptide therapy", "peptide injections") counts on its
    own. A bare compound name only counts alongside one, or in clinic
    context, since "semaglutide" appears on plenty of pages that merely
    discuss it.
    """
    lowered = text.lower()

    for phrase in PEPTIDE_THERAPY_PHRASES:
        idx = lowered.find(phrase)
        if idx == -1:
            continue
        window = lowered[max(0, idx - 25):idx]
        if re.search(r"\b(not|isn't|aren't|never|no longer|n't|don't|do not)\b", window):
            continue
        start = max(0, idx - _EVIDENCE_WINDOW)
        end = min(len(text), idx + len(phrase) + _EVIDENCE_WINDOW)
        return text[start:end].strip()

    if peptide_keywords and is_clinic(text):
        for keyword in peptide_keywords:
            idx = lowered.find(keyword.lower())
            if idx == -1:
                continue
            start = max(0, idx - _EVIDENCE_WINDOW)
            end = min(len(text), idx + len(keyword) + _EVIDENCE_WINDOW)
            return text[start:end].strip()

    return None


def detect_peptides(text: str, peptide_keywords: List[str]) -> List[str]:
    """Which tracked compounds this clinic names, in the order listed."""
    lowered = text.lower()
    return [keyword for keyword in peptide_keywords if keyword.lower() in lowered]


def is_telehealth(text: str) -> bool:
    """True if the clinic treats patients remotely (nationwide reach, and a
    different conversation than a single-location practice)."""
    return _count_signals(text.lower(), TELEHEALTH_SIGNALS) >= 1


def classify_clinic_type(text: str) -> str:
    """Bucket a clinic by what it primarily practices.

    Whichever bucket the site's own copy leans into hardest wins. Ties break
    toward the earlier, more specific bucket -- a practice that names both
    hormone therapy and weight loss once each is a hormone clinic, because
    that is the practice the peptide conversation goes through.
    `wellness_clinic` is both the last bucket and the catch-all for a
    peptide-offering practice that leans into none of them.
    """
    lowered = text.lower()
    scores = [
        ("hormone_clinic", _count_signals(lowered, HORMONE_SIGNALS)),
        ("regenerative_clinic", _count_signals(lowered, REGENERATIVE_SIGNALS)),
        ("weight_loss_clinic", _count_signals(lowered, WEIGHT_LOSS_SIGNALS)),
        ("med_spa", _count_signals(lowered, MED_SPA_SIGNALS)),
        ("wellness_clinic", _count_signals(lowered, WELLNESS_SIGNALS)),
    ]
    best_type, best_score = max(scores, key=lambda pair: pair[1])
    return best_type if best_score > 0 else "wellness_clinic"


def mentions_city(text: str, city: str) -> bool:
    """True if the site names the city the query targeted -- a cheap check
    that a search result for "peptide therapy Austin TX" is really an Austin
    practice and not a national site that ranked for the query."""
    if not city:
        return False
    return re.search(r"\b" + re.escape(city) + r"\b", text, re.IGNORECASE) is not None


def evaluate(
    text: str,
    peptide_keywords: List[str],
    queried_city: Optional[str] = None,
) -> Tuple[Optional[str], dict]:
    """Full clinic assessment of a site's text.

    Returns (skip_reason, details). skip_reason is None when the site
    qualifies as a peptide-offering US clinic; otherwise it names the check
    that rejected it, which the agent counts in its run stats.
    """
    details = {
        "clinic_type": None,
        "peptide_evidence": None,
        "peptides_offered": [],
        "telehealth": False,
        "city": None,
    }

    if is_research_vendor(text):
        return "skipped_research_vendor", details
    if not is_clinic(text):
        return "skipped_not_a_clinic", details

    evidence = find_peptide_evidence(text, peptide_keywords)
    if not evidence:
        return "skipped_no_peptides", details

    details["peptide_evidence"] = evidence
    details["peptides_offered"] = detect_peptides(text, peptide_keywords)
    details["clinic_type"] = classify_clinic_type(text)
    details["telehealth"] = is_telehealth(text)
    if queried_city and mentions_city(text, queried_city):
        details["city"] = queried_city
    return None, details
