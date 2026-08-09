"""Heuristics for classifying a candidate company's target type and
whether it appears to be US-based, from the visible text of its site.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

US_STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

US_STATE_NAMES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
]

_STATE_ABBR_RE = re.compile(r"\b(" + "|".join(sorted(US_STATE_ABBREVIATIONS)) + r")\b")
_STATE_NAME_RE = re.compile("|".join(re.escape(n) for n in US_STATE_NAMES), re.IGNORECASE)
_US_ZIP_RE = re.compile(r"\b\d{5}(-\d{4})?\b")
_US_MENTION_RE = re.compile(r"\b(United States|U\.S\.A?\.?|USA)\b", re.IGNORECASE)

COMPOUNDING_SIGNALS = [
    "compounding pharmacy", "licensed pharmacist", "prescription required",
    "pcab accredited", "usp <795>", "usp 795", "usp <797>", "usp 797",
    "compounded medication",
]

MANUFACTURING_SIGNALS = [
    "cgmp", "gmp facility", "peptide synthesis", "custom peptide synthesis",
    "api manufacturer", "bulk manufacturer", "manufacturing facility",
    "synthesized in", "manufactured in our", "our lab", "in-house lab",
    "solid-phase", "solid phase", "lyophilized in",
]

# Signals that a site sells directly to buyers itself (a storefront) rather
# than being an information/affiliate page about peptides.
ECOMMERCE_SIGNALS = [
    "add to cart", "add to bag", "buy now", "checkout", "shopping cart",
    "in stock", "out of stock", "free shipping", "shop now", "view product",
    "select options", "quantity", "sku", "subtotal",
]

# Signals of an editorial / affiliate / review site -- these rank well for
# buying queries but are media, not companies that sell peptides.
CONTENT_SITE_SIGNALS = [
    "we may earn a commission", "affiliate link", "affiliate disclosure",
    "editorially independent", "no paid placement", "medically reviewed by",
    "fact-checked", "our editorial team", "this article", "table of contents",
    "read our review", "buyer's guide", "buying guide",
]


def guess_us_presence(text: str) -> Tuple[bool, Optional[str]]:
    """Best-effort guess at whether a site's company is US-based, and which
    state, from mentions of "USA"/state names, or a state abbreviation sitting
    next to a zip code (to avoid false positives on stray two-letter words)."""
    state_match = _STATE_NAME_RE.search(text)
    state = state_match.group(0) if state_match else None

    if not state:
        abbr_match = _STATE_ABBR_RE.search(text)
        if abbr_match and _US_ZIP_RE.search(text[abbr_match.end(): abbr_match.end() + 12]):
            state = abbr_match.group(0)

    is_us = bool(_US_MENTION_RE.search(text)) or state is not None
    return is_us, state


def contains_any_keyword(text: str, keywords: List[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


_NEGATION_WINDOW = 25
_NEGATION_RE = re.compile(r"\b(not|isn't|aren't|never|no longer|n't)\b")


def _has_unnegated_signal(lowered_text: str, signals: List[str]) -> bool:
    """True if any signal phrase appears without a negation word (not,
    isn't, never, ...) shortly before it -- companies commonly write
    "we are NOT a compounding pharmacy" as a legal disclaimer, and a plain
    substring match would otherwise misread that as a positive signal."""
    for signal in signals:
        start = 0
        while True:
            idx = lowered_text.find(signal, start)
            if idx == -1:
                break
            window = lowered_text[max(0, idx - _NEGATION_WINDOW):idx]
            if not _NEGATION_RE.search(window):
                return True
            start = idx + 1
    return False


def sells_direct(text: str) -> bool:
    """True if the site looks like a storefront selling to buyers itself."""
    lowered = text.lower()
    return sum(1 for signal in ECOMMERCE_SIGNALS if signal in lowered) >= 2


def manufactures(text: str) -> bool:
    """True if the site claims to synthesize/manufacture its own product."""
    return _has_unnegated_signal(text.lower(), MANUFACTURING_SIGNALS)


def is_content_site(text: str) -> bool:
    """True if the page reads as editorial/affiliate media rather than a
    company selling its own product."""
    lowered = text.lower()
    return sum(1 for signal in CONTENT_SITE_SIGNALS if signal in lowered) >= 2


def classify_company_type(text: str, research_only_evidence: Optional[str]) -> str:
    """Classify a peptide-relevant site into one of the four target
    categories. Assumes the caller has already confirmed peptide relevance
    (e.g. via contains_any_keyword) before calling this."""
    lowered = text.lower()
    if _has_unnegated_signal(lowered, COMPOUNDING_SIGNALS):
        return "compounding_pharmacy"
    if _has_unnegated_signal(lowered, MANUFACTURING_SIGNALS):
        return "manufacturing_lab"
    if research_only_evidence:
        return "research_only"
    return "consumer_and_research"
