"""Mine clinic directories for the businesses they list.

Searching city by city returns tens of leads. A directory lists thousands, and
publishes a sitemap naming every listing page, so one pass over a sitemap is
worth hundreds of searches. This walks those sitemaps, opens each listing, and
pulls out the clinic's own name and website. Output is a "Name|domain" file that
scraper/lead_hunt.py takes as input.

    python3 scraper/dir_mine.py --list
    python3 scraper/dir_mine.py glp1directory --limit 400 --out cands.txt
    python3 scraper/dir_mine.py all --limit 900 --out cands.txt

A cursor per directory is kept in scraper/.dir_cursor so a daily run continues
where the last one stopped instead of re-reading the same listings.
"""
import argparse, concurrent.futures as cf, gzip, html, json, re, ssl, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lead_hunt

CURSOR = HERE / ".dir_cursor"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

US_STATE_SLUG = re.compile(r"-(" + "|".join("""
    alabama alaska arizona arkansas california colorado
    connecticut delaware florida georgia hawaii idaho
    illinois indiana iowa kansas kentucky louisiana
    maine maryland massachusetts michigan minnesota mississippi
    missouri montana nebraska nevada new-hampshire new-jersey
    new-mexico new-york north-carolina north-dakota ohio oklahoma
    oregon pennsylvania rhode-island south-carolina south-dakota tennessee
    texas utah vermont virginia washington west-virginia
    wisconsin wyoming district-of-columbia washington-dc
""".split()) + r")/?$", re.I)

# Each entry: the sitemaps that name listing pages, and the URL fragment that
# marks a listing rather than a category or location page.
DIRECTORIES = {
    "semaglutidenearme": {
        "sitemaps": ["https://semaglutidenearme.org/wp-sitemap-posts-gd_place-1.xml",
                     "https://semaglutidenearme.org/wp-sitemap-posts-gd_place-2.xml"],
        "listing": "/places/",
    },
    "glp1directory": {
        "sitemaps": ["https://glp1directory.com/provider-sitemap.xml",
                     "https://glp1directory.com/provider-sitemap2.xml",
                     "https://glp1directory.com/provider-sitemap3.xml"],
        "listing": "/provider/",
    },
    "globalglp1": {
        "sitemaps": ["https://globalglp1.com/sitemap-clinics.xml"],
        "listing": "/clinic/",
    },
    "mypeptidematch": {
        "sitemaps": ["https://www.mypeptidematch.com/sitemap.xml"],
        "listing": "/clinic/",
    },
    "healingmaps": {
        "sitemaps": ["https://healingmaps.com/listing-sitemap.xml",
                     "https://healingmaps.com/listing-sitemap2.xml",
                     "https://healingmaps.com/listing-sitemap3.xml"],
        "listing": "/listing/",
        # Its slugs end in "-city-state", and a third of the site is ayahuasca
        # retreats in Peru, Costa Rica and Mexico. Requiring a US state in the
        # slug keeps the hormone and weight-loss clinics and skips the rest
        # before a single listing page is fetched.
        "url_must_match": US_STATE_SLUG,
    },
    "peptideclinicfinder": {
        "sitemaps": ["https://peptideclinicfinder.com/sitemap.xml"],
        "listing": "/clinics/",
    },
    "medspadirectorypro": {
        "sitemaps": ["https://medspadirectorypro.com/sitemap.xml"],
        "listing": "/spa/",
    },
    "medspanear": {
        # 60 per-state listing sitemaps, of which only these 51 are US states.
        # The other nine are Baja California, Sonora, Tamaulipas, Navarre, three
        # Italian provinces and two for Queensland; naming the US ones here means
        # a foreign listing is never fetched in the first place.
        "sitemaps": [
                     "https://medspanear.me/api/sitemaps/sitemap-listings-alabama.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-alaska.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-arizona.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-arkansas.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-california.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-colorado.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-connecticut.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-delaware.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-florida.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-georgia.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-hawaii.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-idaho.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-illinois.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-indiana.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-iowa.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-kansas.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-kentucky.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-louisiana.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-maine.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-maryland.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-massachusetts.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-michigan.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-minnesota.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-mississippi.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-missouri.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-montana.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-nebraska.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-nevada.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-new-hampshire.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-new-jersey.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-new-mexico.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-new-york.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-north-carolina.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-north-dakota.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-ohio.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-oklahoma.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-oregon.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-pennsylvania.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-rhode-island.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-south-carolina.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-south-dakota.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-tennessee.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-texas.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-utah.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-vermont.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-virginia.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-washington.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-west-virginia.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-wisconsin.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-wyoming.xml",
                     "https://medspanear.me/api/sitemaps/sitemap-listings-district-of-columbia.xml"],
        # Listings are /<state>/<city>/<clinic-slug>/, so there is no fixed
        # fragment to match on; "listing" only has to appear in every URL and
        # never at the end of one, and url_must_match does the real work.
        "listing": "medspanear.me/",
        "url_must_match": re.compile(r"medspanear\.me/[a-z-]+/[a-z-]+/[a-z0-9-]+/?$"),
    },
    "peptidesuppliermatch": {
        "sitemaps": ["https://peptidesuppliermatch.com/sitemap.xml"],
        # Its 50 /find/states/ pages name clinics but link to none of them; the
        # 1,000 /find/providers/ pages carry the clinic's own site as a plain
        # external link beside a Google Maps address, which is what we need.
        "listing": "/find/providers/",
    },
    "auravenu": {
        # 4,649 US listings at a bare /listings/ path, plus 1,234 Australian ones
        # under /au/listings/. The fragment alone would take both, so
        # url_must_match pins the listing to the root: a clinic page reached
        # through /au/ carries a .com.au site (capsclinic.com.au on the one
        # sampled), which the foreign gate would reject later at a full crawl's
        # cost. Cheaper to never fetch it.
        "sitemaps": ["https://auravenu.com/sitemap-0.xml"],
        "listing": "/listings/",
        "url_must_match": re.compile(r"auravenu\.com/listings/"),
    },
    "medspalistings": {
        # 1,406 /listings/ pages inside a 9,907-URL sitemap that is mostly
        # /states and /city rollups. Coverage of the clinic's own site is
        # partial -- one sampled listing links med-i-spa.com, the next links
        # only the operator's own nap5k.com and a Square booking page -- so
        # expect a lower yield per listing here than the peptide directories.
        "sitemaps": ["https://medspalistings.com/sitemap.xml"],
        "listing": "/listings/",
    },
    "medspafind": {
        # Small and mostly Canadian: 705 URLs, of which 514 are /ca/ and only
        # 184 /us/. The listing fragment is the country segment itself, which
        # is what keeps the Canadian majority out before it is fetched.
        "sitemaps": ["https://medspafind.com/medspas-sitemap.xml"],
        "listing": "/us/",
    },
    "longevityclinicfinder": {
        # 13,951 clinic pages, the largest single directory found so far after
        # medspanear. Its listing pages also carry "related clinic" links, so the
        # clinic's own site is not the only outbound host -- the Hormone Center
        # page links hormonecenter.net a dozen times alongside two other clinics.
        # That is survivable here only because pick_site prefers a link the page
        # labels "Visit website" over any other, which the related links are not.
        # Worth re-checking the first ingest for name/domain mismatches.
        "sitemaps": ["https://www.longevityclinicfinder.com/sitemap/clinics.xml"],
        "listing": "/clinics/",
    },
    "trtguide": {
        # 2,852 URLs, 2,643 of them /clinics/ pages. Both sampled listings link
        # the clinic's own domain plainly (limitlessmewellness.com,
        # agelessmenshealth.com). Slugs carry a Google Place id on the end, which
        # is harmless here -- the extractor reads the page, not the slug.
        "sitemaps": ["https://trtguide.com/sitemap.xml"],
        "listing": "/clinics/",
    },
    "hormonemap": {
        # 1,954 URLs, 1,000 of them /clinics/. Coverage is patchy -- one of two
        # sampled listings had no outbound host at all -- so expect a lower yield
        # per listing than trtguide. dir_mine drops the ones with no website.
        "sitemaps": ["https://hormonemap.com/sitemap.xml"],
        "listing": "/clinics/",
    },
    "ivhealthclinics": {
        # 4,982 URLs, of which 3,144 are /clinics/ pages -- the biggest find
        # since medspanear. The clinic page carries the business's own domain as
        # a plain link (the 4Ever Young Midtown Atlanta page links
        # 4everyoungantiaging.com), so the existing extractor reads it unchanged.
        "sitemaps": ["https://ivhealthclinics.com/sitemap.xml"],
        "listing": "/clinics/",
    },
    "peptidefinder": {
        # 1,541 URLs, of which 881 are /clinic/ pages; the rest are /peptides
        # compound pages and a handful of two-letter state indexes. The clinic
        # page carries the clinic's own domain as a plain link and nothing else
        # outbound -- ways2well.com is the only external host on the Ways2Well
        # page -- so this extractor reads it without any special handling.
        "sitemaps": ["https://peptidefinder.us/sitemap.xml"],
        "listing": "/clinic/",
    },
    "glp1almanac": {
        # Small: 124 URLs, 52 of them /providers/ pages. Worth the one pass it
        # costs, and the cursor means it is skipped cheaply thereafter.
        "sitemaps": ["https://glp1almanac.com/sitemap.xml"],
        "listing": "/providers/",
    },
    "findmyhrt": {
        # 2,316 URLs, of which 398 are /provider/ pages; the other 1,461
        # /hrt-providers/ URLs are state and city indexes, not listings. The
        # provider page carries the clinic's own domain as a plain link
        # (joinmidi.com, myalloy.com) beside RevOffers affiliate links, and
        # revoffers is already in NOT_THE_CLINIC.
        "sitemaps": ["https://www.findmyhrt.com/sitemap.xml"],
        "listing": "/provider/",
    },
    "theivdirectory": {
        "sitemaps": ["https://theivdirectory.com/sitemap.xml"],
        "listing": "/provider/",
    },
    "verifiedantiagingclinics": {
        "sitemaps": ["https://verifiedantiagingclinics.com/sitemap-clinics.xml",
                     "https://verifiedantiagingclinics.com/sitemap-telehealth.xml"],
        "listing": "/clinic",          # matches both /clinics/ and /clinic/
    },
    # Checked and not usable: peptidefinder.us clinic pages link only to
    # themselves, and thepeptidelist.com returns 403 on provider pages. Both have
    # large sitemaps, so they look tempting; they are not worth the fetches.
    # ivtherapymap.com lists 1,281 clinics and links to none of them -- the only
    # external host on a listing page is googletagmanager.
    # peptidescertified.com is abandoned: its robots.txt points the sitemap at
    # caillou.odns.fr, an unrelated French domain. directory.worldlinkmedical.com
    # publishes no sitemap at all.
    # usmedspadirectory.com lists 611 spas and every single "website" it gives
    # ends in .example.com -- the whole directory is invented, so nothing from it
    # can be trusted. medspafind.com renders its US listings in JavaScript, so the
    # clinic's own site never appears in the HTML. medicalspalocator.com claims
    # 18,000 providers but answers 429 to everything, even its sitemap.
    # peptidemap.com publishes 4,149 URLs and none of them is a clinic: 3,814
    # /product/ pages, plus /compare, /coupon and /encyclopedia. It is a price
    # comparison catalogue, the same category as pepty.app.
    # medspalocator.com claims a medspa sitemap but it holds 28 city pages, not
    # listings, and answers 4xx to them. ivtherapydirectory.com publishes only 39
    # county and 83 state pages -- clinics are named on them but there is no page
    # per clinic, so it needs a list-page extractor rather than this one.
    # A dozen likely names (bhrtdirectory, glp1clinics, hormoneclinicdirectory,
    # regenmeddirectory and so on) have no DNS at all, and another dozen
    # (medspadirectory, findpeptidetherapy, myhormonedoctor, usmedspas,
    # trtclinicsnearme) are parked domains serving a one-URL "/lander" sitemap.
    # Probed Sep 20 and rejected, all for the same reason -- a directory that
    # names clinics but links to none of them:
    #   thepeptidefinders.com claims 2,140 listings; its sitemap holds 97
    #     /clinics/<state> pages, and the only external links on them are to
    #     glpfinders.com, longevityfinders.com, robofinders.com and
    #     theaiagentmarket.com -- one operator's SEO network, not clinics.
    #   peptidesnearby.com city pages carry no external host but Google fonts
    #     and Tag Manager.
    #   peptideassociation.org has a provider directory on the page but nothing
    #     in its sitemap: 273 /peptides and 196 /blog URLs, no clinic pages.
    #   peptidetreatments.com is a content site; its sitemap index names
    #     pages, peptides, conditions, symptoms, interactions and guides, and
    #     no provider sitemap at all.
    #   bioidenticaldoctors.com publishes 118 flat <state>.html pages with no
    #     page per clinic -- a list-page extractor job, like
    #     ivtherapydirectory.com.
    #   peptidetherapylocator.com and evexipel.com answer 403 to robots.txt and
    #     both sitemap paths.
    # Probed Sep 22:
    #   ivtherapymap.com has 1,282 /clinics/ pages and looks ideal until you
    #     open one: three sampled listings (Drip Hydration San Diego, Bounce
    #     Hydration Houston, IV Essence San Antonio) carry exactly one outbound
    #     host between them, ivtherapyfinder.com -- the operator's own network,
    #     never the clinic. The thepeptidefinders.com pattern.
    #   findlongevitymd.com runs a WordPress directory plugin whose listing
    #     sitemap holds exactly one entry. Nothing to mine.
    #   trt-finder.com (658 /clinic/ pages) is international: the second listing
    #     sampled was androclinics.com.au, a Sydney clinic, and the first carried
    #     no outbound host at all. The foreign gate would catch the .com.au ones
    #     eventually, but only after a full crawl each, and nothing in the
    #     listing URL says which country a page is, so there is no cheap filter.
    #   trtscout.com and findlocaltrtdoctors.com serve no sitemap.
    #   well-viahealth.com serves no sitemap at any of the usual paths.
    #   thepeptidelist.com was retried with full browser headers and a referer
    #     and still answers 403 "Your request was blocked" on provider pages
    #     while serving its sitemap. Still needs a different fetcher.
    #   extension.health is an 11-URL clinic site, not a directory.
    # Probed Sep 21, same failure mode -- listed but not linked:
    #   thepeptidelist.com publishes 379 /providers/ pages in a 1,409-URL
    #     sitemap, which would be the best find of the night, but the provider
    #     pages answer 403 to a direct fetch while the sitemap serves fine. It
    #     needs a different fetcher, not a different config, so it is parked
    #     here rather than added.
    #   glp1clinics.org advertises "9,700+ clinics in 2,000+ cities" and its
    #     sitemap holds 101 /glp1-clinics/<state> pages -- state rollups, the
    #     bioidenticaldoctors.com shape, so a list-page extractor job.
    #   pathtopeptides.com is a content site: 480 URLs, all flat .html guides
    #     like /where-to-buy-tirzepatide.html, with Spanish duplicates.
    #   peptidebase.io answers 403 to both sitemap paths despite advertising
    #     2,226 providers.
    #   glp-1finder.com (59 URLs) and glp1.healthcare (114) are too small to
    #     carry a per-clinic page and hold city pages instead.
    #   testosteronereplacementdoctors.com and bioidenticalhormonedoctors.com
    #     are one operator on the same Avada WordPress theme, and both publish a
    #     seven-URL page sitemap -- home, contact, about, privacy, disclaimer
    #     and "join the directory". The provider listings never appear in it.
    #   trtanswers.com is a 15-page content site (/trt-labs-explained.html and
    #     the like), and trtclinicguide.com does not answer at all.
    # klinic.com looks like the biggest prize of all -- 663 sitemaps covering
    # wegovy, zepbound, saxenda and TRT in every state -- but it is a telehealth
    # service writing city pages about itself, not a directory: its city pages
    # carry no link to any clinic but its own. americanmedspa.org publishes
    # articles and its sponsors, not its member spas.
}

# Links on a listing page that are never the clinic's own site.
NOT_THE_CLINIC = re.compile(
    r"(^|\.)(healingmaps|semaglutidenearme|glp1directory|globalglp1|mypeptidematch|peptidefinder|"
    r"thepeptidelist|locatepeptides|peptideassociation|pathtopeptides|peptidesuppliermatch|"
    r"peptidescertified|facebook|instagram|twitter|x|linkedin|youtube|tiktok|pinterest|google|"
    r"gstatic|googleapis|gravatar|wp|w3|gmpg|schema|recaptcha|unpkg|jsdelivr|cloudflare|cloudfront|"
    r"bootstrapcdn|fontawesome|jquery|revoffers|doubleclick|googletagmanager|wixstatic|shopify|"
    r"squarespace|yelp|zocdoc|healthgrades|vagaro|booksy|groupon|mapquest|apple|bing|yahoo|amazon|"
    # ad, analytics and consent networks, which appear on every listing page
    r"mediavine|taboola|outbrain|adsystem|adservice|scorecardresearch|quantserve|hotjar|segment|"
    r"onetrust|cookielaw|cookiedatabase|clarity|hubspot|intercom|calendly|linktr|bit|goo|tinyurl|"
    # widgets and reference sites carried by healingmaps listings
    r"vimeo|recaptcha|npiregistry|hhs|maps|nih|who|supabase|builder|example|"
    # medspalistings.com puts its operator's own nap5k.com on listing pages,
    # sometimes as the only outbound link, so it would be read as the clinic
    r"nap5k|auravenu|medspalistings|medspafind|ahrefs|squareup|glp1almanac|"
    # smbpulse.co is longevityclinicfinder's own operator domain and sits on
    # every one of its 13,951 listing pages
    r"smbpulse|longevityclinicfinder|ivhealthclinics|trtguide|hormonemap|"
    # CDNs and analytics carried by theivdirectory listings
    r"googleusercontent|contentsquare|squarespace-cdn|gstatic|cloudinary|imgix|"
    # site builders' asset hosts, booking platforms and free blog hosts: a clinic
    # reached only at one of these has no domain of its own to write to
    r"website-files|cdn-website|wixsite|weebly|blogspot|wordpress|godaddysites|myshopify|"
    r"mypatientnow|zenoti|mindbodyonline|squareup|acuityscheduling|setmore|"
    r"schedulicity|janeapp|simplepractice|clinicsense|tebra|healow|"
    # agencies whose own site is linked from the listings they build
    r"plastixmarketing)\.", re.I)
# anchors a directory uses for the business's own site
WEBSITE_ANCHOR = re.compile(r">\s*(visit\s*(the\s*)?(website|site)|website|official\s*site|"
                            r"go\s*to\s*website|book|visit)\s*<", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def fetch(url, cap=600_000, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        b = r.read(cap)
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    return b.decode("utf-8", "replace")


def listing_urls_by_sitemap(name):
    """{sitemap url: [listing urls]}, omitting any sitemap that would not load.

    Kept per sitemap rather than concatenated because the cursor is an offset,
    and an offset into a list assembled from several sitemaps only means the
    same thing if every one of them loads every time. medspanear.me alone has
    51 state sitemaps and starts resetting connections under 16 workers, so a
    handful drop out on any given run. Concatenated, that shifts every listing
    after the gap: some get mined twice and some are skipped for good. Per
    sitemap, a failure costs nothing but this run's share of that one state.
    """
    cfg = DIRECTORIES[name]
    keep = cfg.get("url_must_match")
    out, seen = {}, set()
    for sm in cfg["sitemaps"]:
        t = None
        for attempt in range(2):
            try:
                t = fetch(sm, cap=5_000_000, timeout=30)
                break
            except Exception as e:
                # medspanear.me resets connections when several of its 51 state
                # sitemaps are pulled at once; the same URL fetched on its own a
                # moment later returns 200. One unhurried retry recovers most of
                # them, and the per-sitemap cursor means the rest cost nothing.
                if attempt == 0:
                    time.sleep(3)
                    continue
                print(f"  sitemap failed {sm}: {type(e).__name__}")
        if t is None:
            continue
        urls = []
        for u in re.findall(r"<loc>([^<]+)</loc>", t):
            if (cfg["listing"] in u and not u.rstrip("/").endswith(cfg["listing"].strip("/"))
                    and (not keep or keep.search(u)) and u not in seen):
                seen.add(u)
                urls.append(u)
        out[sm] = urls
    return out


def listing_urls(name):
    urls = []
    for v in listing_urls_by_sitemap(name).values():
        urls += v
    return urls


def host_of(url):
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower().split(":")[0]


def clinic_of(url):
    """(name, domain) for one listing page, or None when it names no website.

    Picking the first external link is not enough: listing pages carry ad and
    analytics domains, and several directories link to themselves. So the
    directory's own host is excluded, known networks are excluded, and a link the
    page labels "Visit website" wins over a bare one.
    """
    try:
        h = fetch(url)
    except Exception:
        return None
    m = TITLE_RE.search(h)
    # unescape before splitting: "Aura &amp; Sol Aesthetics" must not reach the
    # queue with the entity still in it, or it prints literally in a subject line
    title = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()) if m else ""
    # directory titles read "Clinic Name - Directory" or "Clinic Name: Profile in City"
    name = re.split(r"\s+[-|–—:]\s+|\s+\|\s+", title)[0][:70] if title else ""
    self_host = host_of(url)
    self_base = self_host.split(".")[0]

    def usable(d):
        if "." not in d or d.endswith((".png", ".jpg", ".css", ".js", ".svg")):
            return False
        if d == self_host or d.endswith("." + self_host) or self_base in d:
            return False
        return not NOT_THE_CLINIC.search(d + ".")

    # a link the page labels as the business's website beats any other
    for mm in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.{0,80}?)</a>', h, re.S | re.I):
        href, inner = mm.group(1), mm.group(2)
        if WEBSITE_ANCHOR.search(">" + inner + "<") and usable(host_of(href)):
            return (name or host_of(href), host_of(href))
    for href in re.findall(r'href="(https?://[^"]+)"', h):
        d = host_of(href)
        if usable(d):
            return (name or d, d)
    return None


def cursors():
    """{directory: {sitemap url: offset}}.

    The file used to hold one integer per directory. Those are migrated under
    the key "*", which means "this many listings into the directory as a whole,
    counted the old way" -- honoured once, for directories with a single
    sitemap, and otherwise treated as spent so nothing is re-mined wholesale.
    """
    try:
        raw = json.loads(CURSOR.read_text())
    except Exception:
        return {}
    return {k: ({"*": v} if isinstance(v, int) else v) for k, v in raw.items()}


def start_offsets(cur, name, by_sm):
    """{sitemap: how many of its listings are already mined}.

    A legacy "*" offset counted into the old concatenation, so it is spent
    greedily in the order DIRECTORIES lists the sitemaps -- exactly how it was
    accumulated. Doing anything else would re-mine what it already covers:
    healingmaps is finished at 2,272 and verifiedantiagingclinics stands at
    3,149 of 4,443, and both are multi-sitemap.
    """
    c = cur.get(name, {})
    out = {sm: c[sm] for sm in by_sm if sm in c}
    left = c.get("*")
    if left is None:
        return {sm: out.get(sm, 0) for sm in by_sm}
    for sm in DIRECTORIES[name]["sitemaps"]:
        if sm in out or sm not in by_sm:
            continue
        n = min(left, len(by_sm[sm]))
        out[sm] = n
        left -= n
    return {sm: out.get(sm, 0) for sm in by_sm}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory", nargs="?", default="all")
    ap.add_argument("--limit", type=int, default=400, help="listing pages to open this run")
    ap.add_argument("--out", default="candidates.txt")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for n in DIRECTORIES:
            print(f"{n:<22} {len(listing_urls(n)):>5} listings in sitemap")
        return

    names = list(DIRECTORIES) if a.directory == "all" else [a.directory]
    cur = cursors()
    known_bases, _ = lead_hunt.known()
    rows, opened = [], 0

    # Read every sitemap first, so the limit can be shared out over the
    # directories that still have listings. Splitting it evenly over all of them
    # means a read-out directory silently eats its share: asking for 3,100 with
    # four of seven exhausted returned 903. Read once and reused below -- the
    # loop used to fetch every sitemap a second time, which doubled the load on
    # medspanear.me and is part of why it started resetting connections.
    by_sm = {n: listing_urls_by_sitemap(n) for n in names}
    off = {n: start_offsets(cur, n, by_sm[n]) for n in names}
    unmined = {n: {sm: urls[off[n][sm]:] for sm, urls in by_sm[n].items()} for n in names}
    pool = {n: sum(len(v) for v in unmined[n].values()) for n in names}
    live = [n for n in names if pool[n]]
    take, left = {}, a.limit
    for i, n in enumerate(live):
        share = min(pool[n], max(1, left // (len(live) - i)))
        take[n] = share
        left -= share

    for n in names:
        want = take.get(n, 0)
        batch, starts = [], {}
        # Round-robin across the directory's sitemaps rather than draining the
        # first: with 51 states, taking a run's whole share from Alabama would
        # mean Wyoming is never reached.
        rr = [sm for sm in unmined[n] if unmined[n][sm]]
        i = 0
        while len(batch) < want and rr:
            sm = rr[i % len(rr)]
            pos = starts.get(sm, 0)
            if pos < len(unmined[n][sm]):
                batch.append(unmined[n][sm][pos])
                starts[sm] = pos + 1
                i += 1
            else:
                rr.remove(sm)
        if take.get(n):
            print(f"{n}: {pool[n]} unmined across {len(by_sm[n])} sitemap(s), taking {len(batch)}")
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            for got in ex.map(clinic_of, batch):
                opened += 1
                if got:
                    rows.append(got)
        # Write an offset for every sitemap, not just the ones drawn from, so
        # the legacy "*" can be retired in one go rather than lingering and
        # being re-spent on the next run.
        cur[n] = {sm: off[n][sm] + starts.get(sm, 0) for sm in by_sm[n]}

    CURSOR.write_text(json.dumps(cur, indent=1))
    # Re-read what the campaign holds, now that the crawl is over. known_bases was
    # read at startup, and a mine runs for the best part of an hour while the hourly
    # waves keep ingesting leads behind it -- on Sep 20 that gap let 58 clinics
    # through that the previous wave had queued while this mine was still crawling,
    # and every one of them cost a full lead_hunt crawl before the ingest rejected
    # it by email. Three file reads here save that.
    known_bases, _ = lead_hunt.known()
    seen, keep, dupes = set(), [], 0
    for name, dom in rows:
        if dom in seen:
            continue
        seen.add(dom)
        b = lead_hunt.ss.brand_key(dom)
        if b and b in known_bases:
            dupes += 1
            continue
        keep.append(f"{name}|{dom}")
    Path(a.out).write_text("\n".join(keep), encoding="utf-8")
    print(f"\nopened {opened} listings -> {len(rows)} with a website -> {len(keep)} new "
          f"({dupes} already in the campaign)")
    print(f"wrote {a.out}")
    print(f"next: python3 scraper/lead_hunt.py {a.out} hunt.csv --workers=16")


if __name__ == "__main__":
    main()
