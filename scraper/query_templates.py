"""Search-query templates for each target company type.

Each template must contain a "{peptide}" placeholder, filled in with one
keyword from peptide_keywords.txt to build the final query string sent to
a search or directory provider.
"""

QUERY_TEMPLATES = {
    "research_only": [
        '"{peptide}" research peptides supplier "for research use only" USA',
        '"{peptide}" research chemicals supplier "not for human consumption"',
    ],
    "consumer_and_research": [
        'buy "{peptide}" peptides online USA',
        '"{peptide}" peptides for sale shop',
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
