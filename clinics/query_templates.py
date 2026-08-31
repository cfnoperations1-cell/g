"""Search-query templates for finding US medical clinics that offer peptides.

Unlike the vendor scraper, which searches a national market, clinics are
local businesses -- so every template is geo-targeted. Each one is formatted
with three keys:

    {service}  one line from clinic_services.txt
    {city}     one city from us_cities.txt
    {state}    that city's two-letter state abbreviation

Crossing ~450 cities with ~28 services and the templates below gives a query
space in the hundreds of thousands, which is what lets the agent keep turning
up clinics it has never seen before, day after day. clinics/agent.py walks
that space with a saved cursor so each run picks up where the last stopped.

A template does not have to use every key -- city-only templates broaden the
net for clinics whose sites never name a specific compound.
"""

# Queries aimed at a search API (Serper / Brave / Google CSE / Bing).
SEARCH_TEMPLATES = [
    '"{service}" clinic {city} {state}',
    '"{service}" near {city} {state} book appointment',
    'peptide therapy clinic {city} {state} "{service}"',
    'med spa {city} {state} "{service}"',
    'hormone clinic {city} {state} peptide therapy',
    'anti-aging clinic {city} {state} "peptide therapy"',
    'medical weight loss clinic {city} {state} "{service}"',
    'wellness center {city} {state} "peptide injections"',
    'regenerative medicine clinic {city} {state} peptides',
    'functional medicine {city} {state} "peptide therapy" new patients',
    '"{service}" {city} {state} "schedule a consultation"',
    'telehealth peptide clinic {state} "{service}"',
]

# Queries aimed at a business-directory API (Google Places). Places matches on
# business category and location, so these read like a map search rather than
# a web search.
DIRECTORY_TEMPLATES = [
    'peptide therapy clinic in {city}, {state}',
    'med spa in {city}, {state}',
    'hormone replacement clinic in {city}, {state}',
    'medical weight loss clinic in {city}, {state}',
    'anti-aging and wellness clinic in {city}, {state}',
    'regenerative medicine clinic in {city}, {state}',
]
