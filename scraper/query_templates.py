"""Search-query templates for each target company type.

Vendor templates (QUERY_TEMPLATES) contain a "{peptide}" placeholder, filled
in with one keyword from peptide_keywords.txt. They describe national
suppliers, so no location goes into them.

Local templates (LOCAL_QUERY_TEMPLATES) contain a "{city}" placeholder
instead, filled in from scraper/cities.txt: med spas and clinics are found
city by city, and these queries suit Google Places best (it returns the
business's website, phone and address in one call).
"""

QUERY_TEMPLATES = {
    "research_only": [
        '"{peptide}" research peptides supplier "for research use only" USA',
        '"{peptide}" research chemicals supplier "not for human consumption"',
        '"{peptide}" "research use only" "add to cart" -reddit -forum',
        'buy "{peptide}" research peptide "COA" third party tested USA',
        # product-page and platform-footprint angles surface storefronts the
        # generic queries miss (Shopify/WooCommerce shops, direct product URLs)
        '"{peptide}" inurl:product research peptide',
        '"{peptide}" research peptide "cart" "checkout" -amazon -ebay',
        '"{peptide}" peptide vial "certificate of analysis" buy',
    ],
    "consumer_and_research": [
        'buy "{peptide}" peptides online USA',
        '"{peptide}" peptides for sale shop',
        '"{peptide}" peptides "made in USA" shop "add to cart"',
        '"{peptide}" "our lab" peptides buy third-party tested',
        '"{peptide}" peptides "synthesized in" USA store',
    ],
    "compounding_pharmacy": [
        '"{peptide}" compounding pharmacy USA',
        '"{peptide}" compounded prescription pharmacy',
    ],
    "manufacturing_lab": [
        '"{peptide}" peptide manufacturer USA',
        '"{peptide}" custom peptide synthesis lab',
    ],
}

LOCAL_QUERY_TEMPLATES = {
    "med_spa": [
        "med spa peptides {city}",
        "medical spa peptide therapy {city}",
        "med spa BPC-157 {city}",
        "med spa semaglutide tirzepatide {city}",
        "med spa NAD+ injections {city}",
        "aesthetics clinic peptide injections {city}",
    ],
    "clinic": [
        "peptide therapy clinic {city}",
        "anti-aging clinic peptides {city}",
        "hormone clinic peptide therapy {city}",
        "wellness clinic BPC-157 {city}",
        "longevity clinic peptides {city}",
        "IV therapy clinic NAD+ peptides {city}",
        "men's health clinic sermorelin {city}",
    ],
}

LOCAL_COMPANY_TYPES = tuple(LOCAL_QUERY_TEMPLATES)
ALL_COMPANY_TYPES = tuple(QUERY_TEMPLATES) + LOCAL_COMPANY_TYPES
