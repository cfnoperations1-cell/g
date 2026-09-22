"""Standalone, dependency-free send engine for the outreach campaign.

Drives real sends (initial outreach + timed follow-ups) with durable tracking, using
ONLY the Python standard library so the hourly loop runs under plain `python3`
after any container/clone restart. All state lives in files committed to the repo.

    python3 outreach/serve_send.py stats
    python3 outreach/serve_send.py migrate                    # upgrade sent_log.csv schema in place
    python3 outreach/serve_send.py next 100 batch.json        # build the next wave (follow-ups due first,
                                                              #   then initial sends, vendors before med spas)
    python3 outreach/serve_send.py record batch.json [UPTO] [--skip 3,7]
                                                              # mark rows 1..UPTO of the batch as sent
    python3 outreach/serve_send.py mark replied|bounced|unsubscribed addrs.txt
                                                              # stop follow-ups for these emails/domains

State files (all under outreach/, all committed):
  draft_queue.csv  - every verified contact with its rendered initial subject/body (built by
                     emailer/draft_queue.py --build)
  sent_log.csv     - one row per contact we have emailed: when, how many follow-ups, status
  draft_ids.csv    - optional email -> Gmail draft id for contacts that already have a
                     verified draft; those are sent by draft id (which also clears the draft)

Follow-up cadence: FOLLOWUP_DAYS after the last touch, up to MAX_FOLLOWUPS per contact, and
never to anyone whose status is not "active" (replied / bounced / unsubscribed).
DAILY_CAP guards Google Workspace's daily sending limit; the wave size is clipped to it.
"""
import csv, html, json, os, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

csv.field_size_limit(10_000_000)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outreach"
QUEUE = OUT / "draft_queue.csv"
SENT = OUT / "sent_log.csv"
DRAFT_IDS = OUT / "draft_ids.csv"
EXPECTED = OUT / "exclusions_expected.txt"
_THIRD_PARTY = None
FU_TPL = {"medspa": ROOT / "emailer" / "followup_medspa.txt", "vendor": ROOT / "emailer" / "followup_vendor.txt"}

DAILY_CAP = int(os.environ.get("DAILY_CAP", "100"))      # ramp: 100 per day (Gmail throttled at ~220)
HOURLY_CAP = int(os.environ.get("HOURLY_CAP", "10"))     # ramp: 10 per hourly wave
FOLLOWUP_DAYS = int(os.environ.get("FOLLOWUP_DAYS", "3"))
MAX_FOLLOWUPS = int(os.environ.get("MAX_FOLLOWUPS", "3"))
# Follow-ups are OFF. Jonathan, Sep 21: "do not send any follow up emails without
# my approval" -- every wave is first contact only until he says otherwise. The
# default is 0 rather than an env var so a scheduled wave cannot drift back.
FU_SHARE = float(os.environ.get("FU_SHARE", "0"))         # fraction of a wave that may be follow-ups
FOLLOWUP_START = os.environ.get("FOLLOWUP_START", "2026-09-17T18:30:00Z")  # no follow-ups at all before this
PRIORITY_DOMAINS = ["heritagelabsusa.com"]                 # "peptide veterans": the one veteran-owned vendor
# vendors that are obviously not US-based get skipped (the pitch is US-made supply, no customs risk)
FOREIGN = re.compile(r"\.(ca|uk|co\.uk|is|cn|ae|eu|au|de|fr|in|mx|nl|ru|pl|es|it|br|hk|sg|nz|ie|ch|se|no|dk|fi|tw|jp|kr|to)$"
                     r"|costarica|french|german|british|canad|austral|europe|\buae\b|canada|europe|-uk\b|\buk-|uk\.(com|net|org)$", re.I)
# scraped page titles that are not a business name -> fall back to the bare domain
JUNK_VENDOR = re.compile(r"click here|\bpromo\b|\beligible\b|\beditor\b|\bnotes\b|\balternative\b|^visit\b|\bdosing\b|cheapest|^wholesale peptides$|marcus hansen|view source|^source$|^usa$|^recovery$|^peptides?$|^buy\b|for sale|coupon|discount|use code|^code\b|save \d|\d+% off|free shipping"
                         r"|\boffers?\b|wholesale medical|nasal spray|research peptides|→|↗|adipotide|glutathione|^ghrp"
                         r"|^pt$|^best\b|^top\b|\bshop$|\bstore$|^home$|^welcome$|^peptide$|^wholesale$|affiliate|^visit site$"
                         r"|^(high|low|new|free|fast|quality|premium|official|trusted|reliable|verified|tested|pure|safe"
                         r"|secure|online|orders?|products?|research|labs?|login|account|cart|menu|search|sales?|deals?|prices?)$", re.I)


FOREIGN_LOCAL = {"contato", "kontakt", "contacto", "info-de", "info-uk"}
# Company-form suffixes that only ever sit at the END of a name: "AB" is Swedish
# (aktiebolag) and identifies Innovagen AB of Lund, but \bab\b anywhere in a
# name would also hit the queue's "AB Hormone", a US clinic, so it is anchored.
# This list is deliberately short, because the first version was not and it was
# a bad mistake: it carried "spa" for the Italian Societa per Azioni, which
# matched 246 queue rows -- every US business called "... Med Spa", the single
# largest audience in the campaign -- and "sa", which matched "Weight Loss SA",
# San Antonio. Both are gone. Anything ambiguous in English belongs in
# FOREIGN_DOMAINS as a named domain with its evidence, not in a pattern.
FOREIGN_SUFFIX = re.compile(r"\s(ab|oy|oyj|a/s|gmbh|s\.a\.|n\.v\.)\.?$", re.I)
FOREIGN_NAME = re.compile(r"\b(uae|dubai|uk|london|centre|wuhan|shanghai|shenzhen|beijing|hangzhou|guangzhou|nanjing"
                          r"|jinan|qingdao|tianjin|chengdu|xi'?an|suzhou|ningbo|zhengzhou|changsha|hefei|kunming|dalian|chongqing"
                          r"|shijiazhuang|shandong|jiangsu|zhejiang|hubei|hunan|henan|hebei|anhui|sichuan|guangdong"
                          r"|hong kong|gmbh|s\.?r\.?l|b\.?v\.?|pty|sdn bhd|sdn\. bhd|ltd|limited|co\.,? ?ltd|trading co"
                          r"|canada|europe|costa rica|australia|india|china)\b", re.I)
# Place names run together inside a domain, where word boundaries never match:
# shandongyixinpeptides.com is Shandong province. Only tokens long and distinctive
# enough to be safe as substrings belong here -- "india" is left out because it sits
# inside "indiana", and "uk"/"eu" are far too short to risk.
FOREIGN_IN_DOMAIN = re.compile(
    r"shandong|jiangsu|zhejiang|guangdong|sichuan|shaanxi|liaoning|fujian|jiangxi|guizhou"
    r"|wuhan|shanghai|shenzhen|beijing|hangzhou|guangzhou|nanjing|jinan|qingdao|tianjin"
    r"|chongqing|chengdu|suzhou|ningbo|zhengzhou|changsha|kunming|dalian|shijiazhuang|xiamen"
    r"|hongkong|chinese|gmbh|\bsarl\b", re.I)
# Country codes too short to use as substrings anywhere in a domain, but safe at the
# front of one: uaepeptideresearch.com is Dubai, while youngeryouaesthetics.com is a
# US med spa whose name simply runs "yoU AEsthetics" together. \buae\b in FOREIGN
# cannot help -- a run-together domain never offers the word boundary.
FOREIGN_DOMAIN_PREFIX = re.compile(r"^(uae|ksa|qatar|dubai|abudhabi|hk)[a-z0-9]", re.I)
# "hk" joins them because of hkburson.com, whose harvested name is "Hong Kong Burson
# PolyPeptide RD Limited" -- both "hong kong" and "limited" are in FOREIGN_NAME, yet
# nothing fired, because the queue row stores the DOMAIN in business_name and the
# harvested name never reaches the gate. Any gate that reads the name is blind in
# that case, so the domain has to carry the signal by itself.

# Vendors confirmed non-US by looking at the site itself, where the stored name gives
# nothing away. Jinan Boruimei Trading Co., Ltd. is a Chinese trading company: the
# US-made, no-customs pitch has nothing to say to it.
FOREIGN_DOMAINS = {"boruimei.com",      # Jinan Boruimei Trading Co., Ltd.
                   "hnhkpeptide.com",   # "Hongke Biotechnology", a Chinese supplier
                   "peakpeptide.com",   # its own site says "EU Supplier"
                   "spresearchcenter.com",  # contact number on the page is +86 (China)
                   "peptuvia.com",      # marketplace shipping from China warehouses,
                                        # its front page schedules around Chinese holidays
                   "myotrope.com",      # its verified resellers are listed as Netherlands / Europe
                   "24hourpeptides.com",  # prices in GBP, next-day UK shipping, UK company number
                   "uwa-biotech.com",   # WhatsApp contact number is +86 (China)
                   "modernaminos.com",  # the only phone it publishes is +1 437, Toronto
                   "healtlab.com",      # every contact on its page is a +852 (Hong Kong)
                                        # WhatsApp or Telegram, against three gmail addresses
                   "yansenpeptidesfactory.com",  # "a leading peptide raw material manufacturer
                                        # based in China", with a Shenzhen street address
                   "sciencepeptidelab.com",  # "Direct factory supply. No trading intermediaries."
                                        # against two +852 (HK) WhatsApp numbers
                   "walkerchemicals.store",  # "Free delivery on orders over 250 pounds"
                   "homopeptide.co",    # "Ships from China warehouse ... 7-15 business days"
                   "hkroids.com",       # its contact page lists a +86 China number and four
                                        # +852 Hong Kong ones, against @hkroids.net addresses
                   "norcopeptide.com",  # publishes "(254) 607-7119" as if it were a US phone; that
                                        # is its QQ number 2546077119 reformatted, and its other
                                        # number is +852 (Hong Kong)
                   "synth-peptide.com",  # the only phone it publishes is (852) 945-5869, Hong Kong
                                        # dressed up as a US area code
                   "nutrigenixscientific.com",  # "Based in Ontario, Canada", sells "Canada's Most
                                        # Affordable Research Peptides" and prices in CAD. Nothing in
                                        # the domain or the stored name says so -- only the site does
                   "glunovabio.com",    # flies a US flag and trades as Prost Biotech; its About page
                                        # says PROST BIOTECH SDN BHD, "Malaysian Registered Business",
                                        # Bandar Bukit Jalil, Kuala Lumpur, and its only real number is
                                        # +65 (Singapore). The "+1 (628) 555-0193" it gives for its
                                        # account manager is in the 555-01xx range reserved for fiction.
                   "chapeptides.com",   # trades as "CH Peptides Co., Ltd", but its own About page
                                        # says CHA MEDICAL TECHNOLOGY (Guangzhou) CO., LTD, with
                                        # "peptide synthesis capabilities in Guangzhou, China"
                   "peptidechn.com",    # CHN is the country code and the site means it: "our express
                                        # parcels will be concentrated in Hong Kong and then sent to
                                        # the world", against a +852 WhatsApp
                   "purepeptide99.com",  # "Wan Chai District, Hong Kong, China" under two +852
                                        # WhatsApp numbers, and an OEM factory besides
                   "thepeptideco.shop",  # its own title is "Buy peptides Australia online" and the
                                        # currency selector opens on AUD
                   "regena-peptides.com",  # "(c) 2026 Regena Peptides . Marbella", prices in EUR,
                                        # WhatsApp +44 (UK) and +34 (Spain), free shipping over
                                        # EUR 3,500. The .com and the "USA" shipping hub are the
                                        # only American things about it
                   "lipo-peptide.com",  # "Lipo-Peptide Co., Ltd.", 777 Lime Dam, Xiucheng District,
                                        # Hangzhou, Zhejiang Province, P.R. China, two +852 numbers,
                                        # WeChat, and "Factory direct sales" under its product photos
                   "dlpeptides.com",    # its own <title> is "Research Peptides UK Supplier" and its
                                        # contact page is "UK Support & Customer Enquiries", against a
                                        # 07455 UK mobile on WhatsApp
                   "faithfulbio.com",   # "Xi'an Faithful BioTech Co., Ltd", Lianhu District, Xi'an,
                   "faithful-chemical.com",  # Shaanxi, with seven +86 WhatsApp numbers against seven
                                        # sales aliases, and "Steroid Raw Powder" as a product category
                                        # beside the peptides. Its stored phones read "(313) 777-0562;
                                        # (778) 278-0648" -- those ARE the +86 mobiles 13137770562 and
                                        # 17782780648, reformatted into US shape by our own harvester.
                                        # A US-looking number in a discovery row can be this artifact
                   "regenwellph.com",   # "Research Peptides Philippines | COA-Tested, Nationwide
                                        # Delivery -- Regenwell PH", priced in PHP. The stored row
                                        # says us_signal = yes; it is the second follow-up in two
                                        # waves that would have sent the no-customs-risk pitch to a
                                        # company outside the US for the second time
                   "innovagen.com",     # "LL-37 - Innovagen AB": AB is aktiebolag, the Swedish
                                        # company form, and Innovagen is a Lund peptide synthesis
                                        # house. Foreign and our side of the trade at once. The
                                        # suffix is gated above as well, anchored to the end
                   "hkburson.com",      # "Hong Kong Burson PolyPeptide RD Limited", reachable at
                                        # pj91920107@icloud.com, phones (852) 667-1708 and
                                        # (861) 926-3496. Pinned as well as prefix-matched
                   # --- Hong Kong dial code posing as a US area code -------------------
                   # Each of these publishes a phone written "(852) xxx-xxxx". 852 is Hong
                   # Kong's country calling code and is not an assigned NANP area code, so
                   # the number cannot be a US line however it is punctuated. 75 harvested
                   # vendors carry one; these are the ones still live after every other
                   # gate. The queue has no phone column, so this cannot be checked at send
                   # time -- the evidence lives in the exports and the verdict lives here.
                   # Note the harvested us_signal_on_site says "yes" for several of them
                   # (and for Zhengzhou Lan Yun and Jinan Wanfushun, which are Chinese on
                   # their face), so that column does not outvote the dial code.
                   "anlianpeptide.com",     # Anlian Peptide, (852) 133-1115
                   "bantingpeptide.com",    # BanTing Peptide, two 852 lines
                   "peptidesfactorycn.com", # Peptide Factory CN -- "cn" is in the name too
                   "peptidesourcehub.com",  # Peptide Source Hub, (852) 449-0001
                   "qianmiaopeptide.com",   # Qianmiao Peptide, (852) 448-7845
                   "reta-peptide.com",      # Reta-Peptide: an 852 line beside (582) 090-3569,
                                            # which is not an assigned area code either
                   "splabcenter.com",       # SP Lab, two 852 lines beside a Chicago 773
                   "suppeptide.com",        # SUP Peptide, (852) 843-2247; contact "alice" and a
                                            # supvip188@gmail.com second address
                   "wanxipeptide.com",      # Wanxi Peptides, (852) 646-2746, and the harvested
                                            # country is "unknown (not stated on list)"
                   # -------------------------------------------------------------------
                   "jiudingbio.com",    # stored name "Chongqing Jiuding Biotechnology": Chongqing is
                                        # a Chinese municipality the FOREIGN_NAME list lacked until
                                        # this row surfaced; both regexes carry it now, and the
                                        # domain is pinned here so the fix does not rest on the name
                   "btbiolabs.com",     # "B2B Peptide Raw Material Supply for Global Buyers", sold
                                        # OEM/ODM through a WhatsApp quote line on +1 343 635 6770 --
                                        # a 343 is Ontario, the modernaminos.com pattern. It supplies
                                        # raw material to brands, which is our side of the trade
                   "revivpeptides.com",  # "Buy Peptides Canada Online ... Reviv Peptides is a
                                        # Canadian supplier of research peptides. Every batch ships
                                        # from our Vancouver lab", with testimonials from Calgary,
                                        # Edmonton, Winnipeg and Ontario. It had already been sent
                                        # the no-customs-risk pitch once before anyone read the site
                   "nobledragons.com",  # not a peptide business at all: "Estate Direct - Single
                                        # Origin Artisan Chinese Teas". Its order subdomain carries a
                                        # "China warehouse holiday shipping notice ... enhanced
                                        # customs inspections ... shipping pauses September 25 -
                                        # October 7", which is the peptuvia.com pattern exactly. The
                                        # stored row calls it California with us_signal = yes
                   "lanhubio.com",      # its About page: "In response to China's Belt and Road
                                        # Initiative, Lanhu has actively expanded into international
                                        # markets". Its two sales aliases, saleshua@ and saleszhang@,
                                        # are Chinese surnames used as role addresses -- the same
                                        # pattern as the seven at faithful-chemical.com below
                   "yuansensetech.com",  # its own FAQ: "Where are you shipping from? We ship
                                        # from Hong Kong or Shenzhen". The stored discovery row says
                                        # "California" with us_signal = yes, which is simply wrong --
                                        # the row is a starting point for vetting, never the verdict
                   "yidanbio.com",      # "Shanghai Yidan Biotechnology Co., Ltd ... The best factory
                                        # in China", selling ANABOLIC STEROIDS, SARMS and "HGH AND
                                        # PEPTIDES" side by side. Its stored phone "(852) 685-0531"
                                        # is the Hong Kong +852 68505312 cut down to US shape by our
                                        # own harvester -- the jpt.com artifact once more
                   "severnbiotech.com",  # "Severn Biotech Limited, Unit 2, Park Lane,
                                        # Kidderminster, Worcestershire, DY11 6TJ", +44 (0)1562
                                        # 825286 -- named after the English river, not a US state.
                                        # It makes molecular-biology reagents (its front page is
                                        # selling ethidium bromide); the peptide words the crawler
                                        # matched are catalogue entries, not a business that buys
                   "kilobio.com",       # "Ningbo Kilo Biotechnology Co., Ltd., a life-science
                                        # chemistry company based in Ningbo, China", footer address
                                        # Cixi/Yuyao, Ningbo City 315300, P.R.C., with a language
                                        # switcher and a "Factory Area" counter. The inverse of the
                                        # faithful-chemical artifact: its "+1 607 670 0006 (Whatsapp)"
                                        # and "+1 801 820 0009 (Text)" are genuinely US-format numbers,
                                        # bought to front a Chinese factory. A US number proves nothing
                   "jpt.com",           # "JPT Peptide Technologies GmbH, Hermann-Dorner-Allee 23,
                                        # 12489 Berlin, Germany", +49-30-6392-5500. Its stored phones
                                        # read "(202) 895-2019; (322) 980-7878; (888) 578-2660"; 322 is
                                        # not a US area code, because that number is the German
                                        # +49-30-322980-7878 chopped into US shape by our harvester --
                                        # the same artifact documented at faithful-chemical.com below.
                                        # A peptide manufacturer in its own right, not a buyer
                   "jitaibiotech.com",  # "Shandong Jitai Biotech Co., Ltd"; the apex serves a
                                        # Chinese-language site and en.jitaibiotech.com gives the China
                                        # headquarters at No. 22 Jinyu Road, National High-tech
                                        # Development Zone, with "Contact: Mr. Li +86-18660723366".
                                        # It makes cosmetic peptide raw material by the ton -- a
                                        # competing supplier, and a foreign one
                   "clarascience.com",  # its own <title> is "Research peptides -- Australian warehouse,
                                        # documented batches", the shop reads "Most in-demand compounds
                                        # in Australia" and "Built for Australian research"; the
                                        # discovery row had already scored us_signal = no
                   "chemexpress.com",   # a .com with a (609) New Jersey number in its directory
                                        # listing, but the site is a Shanghai CRO/CDMO: "3 Building,
                                        # No. 1999, Zhangheng Road, Pudong New Area, Shanghai,
                                        # P.R.China", +86 (21) 5895-0125, and a Shanghai ICP licence
                                        # in the footer. It makes APIs, so upstream as well as foreign
                   "qyaobio.com",       # trades as QYAOBIO, but its own copy reads "QYAOBIO
                                        # (ChinaPeptides CO., Ltd.) is a professional peptide synthesis
                                        # company ... in China", address SHANGHAI, CHINA, phones +86.
                                        # Foreign and a synthesis house both at once
                   "aotaipeptide.com",  # "No.65 QiShan Street, GuangZhou, GuangDong" under a +852
                                        # WhatsApp; calls itself "a trusted Beauty Peptide factory and
                                        # professional cosmetic peptide manufacturer"
                   "weipeptide.com",    # "Weipeptide Technology Co., Ltd", with a Factory Tour in the
                                        # menu and +852 65670922 as its service hotline
                   "changyuanpolypeptide.com",  # its address is Pingshan District, Shenzhen City, and
                                        # it offers WhatsApp, WeChat and a +852 number
                   "primewaypeptide.com",  # the site behind baiwei@usprimeway.com; the only number it
                                        # publishes is (852) 548-1319, Hong Kong
                   "aurobiopeptide.com",  # "AuroBiopeptide Technology (Shenzhen) Co., Ltd.", Room 905,
                                        # Hi-Tech Park, Nanshan District, Shenzhen, Guangdong, behind
                                        # a +86 primary WhatsApp and three +852 backups
                   "vivpeptide.com",    # trades as VIVA Biotechnology; the three numbers it publishes
                                        # are all (852) Hong Kong, printed like US area codes
                   "genohopebio.com",   # its own <title> is "China HP peptide API ... HP Peptide API
                                        # Factory", the contact page is "China GLP-1 API Manufacturers
                                        # Suppliers Factory", and the phone is a mainland mobile. It
                                        # is also an API factory, so upstream of us either way
                   "gemaihealth.com",   # trades as Genmai Health on a .com; its About page says
                                        # "founded in Xi'an, Shaanxi, China", and the phone in its
                                        # header on every page is +86
                   "sulanpeptides.com",  # "SULAN Peptide Factory": eight sales contacts, every one of
                                        # them a +852 (Hong Kong) WhatsApp, and an FAQ inviting you to
                                        # "visit our factory at any time"
                   "peptiatlas.com",    # the name reads like a reference work; the site is a vendor,
                                        # and its address is "Mingze Industrial Park, Shanghai, China"
                                        # against a +44 UK mobile
                   "dcxpeptides.com",   # trades as "DC Peptide" on a .com, but its About page is
                                        # headed "Dongcheng Technology Co., Ltd." and every number on
                                        # the site is +852 (Hong Kong). Its inquiry form asks for your
                                        # "Phone/WhatsApp/WeChat... (Very important)", and it sells
                                        # custom peptide manufacturing -- upstream of us either way
                   "lyzelabs.com"}      # publishes no address, phone or country anywhere, but its
                                        # shipping page says every order is "dispatched directly from
                                        # our international synthesis laboratories" and it takes UPI,
                                        # India's payment rail. A reseller fronting overseas synthesis
                                        # is neither US-made nor a buyer of US material


# Vendors we decline to approach for reasons that have nothing to do with where
# they are. aminoasylumofficial.com presents itself as the authorized successor to
# Amino Asylum, whose original operation was closed by FDA action in 2025, and at
# least eight lookalike domains trade on that name. Nothing on the page proves
# which one is the real business, and a wholesale pitch sent to the wrong one
# lands in a stranger's inbox under Jonathan's name.
#
# The two peptide "catalog" sites are not suppliers or buyers: thepeptidecatalog.com
# is a price-comparison directory ("Learn Peptides. Get the Best Price.") and
# peptidedosages.com publishes dosing charts. Both list the peptides we make, which
# is why the crawler scored them highly, but neither buys wholesale -- a domestic
# supply pitch is the wrong message and spends a send on a reader, not a customer.
# They could be worth approaching about being listed; that is a different email,
# which Jonathan has not written.
DECLINED_DOMAINS = {"aminoasylumofficial.com",
                    "thepeptidecatalog.com",
                    "peptidedosages.com",
                    "peptidelibrary.app",   # "Compare Peptides, Track Research" -- a reference app
                    "genscript.com",        # GenScript is one of the largest peptide and gene
                                            # synthesis houses in the world, headquartered in Nanjing.
                                            # It manufactures what we manufacture, so it is a
                                            # competitor rather than a buyer, and the only address
                                            # harvested for it was support.eu@, its European desk
                    "peptidestaff.com",     # "Remote Staffing for Peptide Operations" -- it sells
                                            # virtual assistants to peptide companies, not peptides.
                                            # The keyword crawler cannot tell those apart
                    "peptidemanagerpro.com",  # says it outright on its own About page: "Not a vendor.
                                            # We do not sell research compounds. We provide affiliate
                                            # links to independent vendors who do."
                    "riteaid.com",          # the actual Rite Aid: a national retail pharmacy
                                            # chain, reached because it publishes health articles
                                            # ("KPV Peptide: Benefits, Dosage & 2026 Legal Status").
                                            # hello@riteaid.com is not a corporate contact and the
                                            # stored phone is a (863) Florida number, not theirs.
                                            # A cold wholesale API pitch to a public company at a
                                            # harvested address does us no good at all
                    "weightlossproviderguide.com",  # "Best GLP-1 Providers Compared (2026)", whose
                                            # contact page is headed "Editorial & Media" and whose
                                            # about page introduces "Our Editorial Team". It ranks
                                            # providers for readers; it does not buy peptides
                    "bulknaturalswholesale.com",  # "Bulk Organic Body Butters, Carrier & Essential
                                            # Oils". It wholesales cosmetic ingredients and the
                                            # crawler matched it on a NAD+ supplement listing
                    "muscleandbrawn.com",   # "Muscle + Brawn | Buy Peptides, SARMs, TRT, And
                                            # Coaching" -- the page we harvested was its article "4
                                            # Best Peptide Vendors Compared In 2026". It reviews
                                            # vendors and sells coaching; it does not buy peptides
                    "chemyo.com",           # out of business. Every URL on the domain now serves
                                            # one page: "Chemyo permanently closed on September 8,
                                            # 2026 ... no longer accepting new orders", with a warning
                                            # that no other site is authorised to represent it. A
                                            # follow-up asking them to compare wholesale pricing would
                                            # be writing to a company that has shut its doors
                    # Health journalism and advocacy .orgs keep surfacing because they
                    # publish about the compounds we sell. A blanket .org rule would be
                    # wrong -- 55 live queue rows are .org and most are real clinics
                    # (balancedhc.org, walkerwellness.org) -- so they are named here.
                    "amcdefenselaw.com",    # a criminal defence firm, and the page harvested is "The
                                            # DOJ also prosecuted Tailor Made Compound[ing]" -- it
                                            # writes about prosecutions in this exact industry. The
                                            # lumalexlaw.com case, with an edge to it
                    "sussex-research.com",  # both phones are 613, which is Ottawa. A reminder that a
                                            # VALID area code is not a US one: NANP covers Canada and
                                            # the Caribbean, so 613, 416, 604 and the rest pass every
                                            # "is this a real area code" check while being foreign.
                                            # The harvested state says New Hampshire; the numbers do not
                    "bocsci.com",           # its only phone is "(221) 892-7121" and 221 is not an
                                            # assigned NANP area code, so the number cannot be a US
                                            # line. Same tell as the 852 cluster and nuvellolabs
                    "vivabiotech.com",      # stored name is the link label "See Website", no phone
                                            # and no US signal. ViVa Biotech is a contract research
                                            # organisation -- the bioduro.com case in every respect
                    "hubmeded.com",         # "SNAP-8 Peptide: A Non-Invasive Botox Alternative" from
                                            # a medical education hub, as the domain spells out. The
                                            # a4m.com case: it teaches about the compounds
                    "bioduro.com",          # the row says nothing -- the stored name is the link
                                            # label "See Website" and there is no phone -- but
                                            # BioDuro is a contract research and manufacturing
                                            # organisation, the cambrex and genscript case. It is
                                            # paid to make compounds for other people
                    "bscg.org",             # Banned Substances Control Group, a certification and
                                            # testing body. The page harvested is "Retailers Selling
                                            # Unapproved Sports Peptides" -- it publishes lists of
                                            # companies doing what a cold peptide pitch looks like.
                                            # The worst possible recipient, and the third watchdog
                                            # after safemedicines.org and medshadow.org
                    "a4pc.org",             # Alliance for Pharmacy Compounding, a trade association:
                                            # the page harvested is consumer guidance, "questions
                                            # consumers can ask when choosing ...". The
                                            # safemedicines.org and medshadow.org case
                    "raybiotech.com",       # "RayBiotech: Empowering Your Proteomics Research" --
                                            # antibody arrays and ELISA kits, the ibl-america.com
                                            # case. It sells the means to measure, not the product
                    "pepcalc.app",          # harvested name is the link label "Open PepCalc ->", no
                                            # phone and no US signal. A dosing calculator, the
                                            # peptidelibrary.app case: a tool, not a business
                    "peptidekit.app",       # "Open PeptideKit ->", same shape and same verdict
                    "ils-lab.com",          # "Peptide QC & Analytical Testing | ILS Lab"
                    "optiqhealthlabs.com",  # "Peptide Testing Services in Colorado" -- with
                                            # checkpeptide, cellorigins, freedomdiagnostics, ibl-america
                                            # and janoshiklab, the sixth testing house declined. They
                                            # keep surfacing because they rank for the compounds we sell
                    "a4m.com",              # the American Academy of Anti-Aging Medicine: the harvested
                                            # name is "Peptide therapy handbook for healthcare ...", and
                                            # there is no US signal on the row. A teaching and
                                            # certification body, the empiremedicaltraining.com case
                    "myspalive.com",        # the page harvested is "Peptide Distributor Sign Up |
                                            # Wholesale" -- it is recruiting distributors, so it is the
                                            # supplier in that relationship. The peptidedropship.com case
                    "pepticom.com",         # a judgement call, recorded as one: the stored name is just
                                            # the brand twice, no country is given, and the single phone
                                            # is "(972) 549-5549". 972 is a real Dallas area code, so
                                            # this is not the clean 852 tell -- but it is also Israel's
                                            # country code and 54 is an Israeli mobile prefix, and
                                            # Pepticom is a Jerusalem company. With the US pitch resting
                                            # on domestic manufacture, the campaign errs toward excluding
                                            # on geography rather than loosening the gate
                    "ibl-america.com",      # the harvested name is a catalogue entry, "Glucagon-like
                                            # Peptide-1 (GLP-1) Active Form ELISA - IBL-America". An
                                            # ELISA is an assay kit: this house sells the means to
                                            # measure peptides, not peptides
                    "lifetein.com",         # "LifeTein: Custom Peptide Synthesis Services" -- it
                                            # synthesises peptides to order, which is what we do. The
                                            # genscript and cambrex case, a competitor not a buyer
                    "bcnpeptides.com",      # the row is malformed -- the stored vendor name is a URL,
                                            # "https://www.bcnpeptides.com/successful..." -- so nothing
                                            # describes the business, and there is no US signal. BCN is
                                            # Barcelona, and the company synthesises peptides: foreign
                                            # and our side of the trade both
                    "vitalibrary.com",      # "Ipamorelin: Uses and Benefits, Mechanism of Action" --
                                            # a reference library, as the domain says. It explains
                                            # compounds, it does not stock them
                    "freedomdiagnosticstesting.com",  # "Freedom Diagnostics Testing - Reliable.
                                            # Ac[curate]" -- a testing lab, the fourth of them this
                                            # week after checkpeptide, cellorigins and janoshiklab.
                                            # It sells results, not product
                    "medshadow.org",        # MedShadow Foundation, a nonprofit covering drug side
                                            # effects; the harvested name is one of its headlines,
                                            # "FDA Recalls and Warnings: Glutathione Peptide ..."
                    "healthletic.io",       # "Complete List of Peptides and What They Do" -- an
                                            # affiliate content site, the nootropicsexpert.com case
                    "cambrex.com",          # the harvested name is a press release headline, "Cambrex
                                            # Expands Peptide Manufactur[ing]". Cambrex is a contract
                                            # manufacturer that makes peptides to order -- the genscript
                                            # case exactly, our side of the trade rather than a buyer
                    "canpeptide.com",       # the row does not describe this domain at all: it stores
                                            # the vendor name "American Peptides" (americanpeptide.co is
                                            # already declined below), a Louisiana area code and the
                                            # state California. Nothing in it vouches for canpeptide.com,
                                            # whose own name reads Canadian, so there is no US evidence
                                            # to send on
                    "cellorigins.com",      # "Cell Origins | Expert Phage Display ..." -- phage display
                                            # is an antibody discovery service. A services lab like
                                            # checkpeptide.com and janoshiklab.com: it runs assays for
                                            # other people, it does not buy peptides wholesale
                    "safemedicines.org",    # the Partnership for Safe Medicines: a .org advocacy
                                            # group campaigning against counterfeit and unsafe drug
                                            # supply, reachable at editors@. The harvested row is the
                                            # title of an explainer, "What is a 503B outsourcing
                                            # facility", and its phone column holds a URL. Not a buyer,
                                            # and the last organisation to cold-pitch peptides to
                    "risingtrends.co",      # the harvested name is a sentence out of an article --
                                            # 'A 2025 lab analysis of "research-only" p...' -- and
                                            # there is no US signal. A site writing about the
                                            # category, not one buying in it
                    "nootropicsexpert.com", # "The Most Comprehensive Nootropics List" -- one writer's
                                            # affiliate content site, reachable at david@. The
                                            # muscleandbrawn.com and healingmaps.com case: it writes
                                            # about compounds, it does not stock them
                    "tnjone.com",           # "TN Jone", no US signal, no stated country, and one of
                                            # its two phones is "(861) 806-..." -- 861 is not an
                                            # assigned NANP area code, the same tell as wanfs.cn
                                            # (Jinan Wanfushun), which carries an 861 line too
                    "checkpeptide.com",     # harvested name is "CheckPeptide - Trusted Testing ...",
                                            # and the address is labs@. An analytical lab that tests
                                            # other people's peptides, the janoshiklab case below --
                                            # it sells assays, it does not buy peptides
                    "nuvellolabs.com",      # both phones are impossible as US lines: "(516) 147-3085"
                                            # has an exchange starting with 1, which NANP forbids, and
                                            # 568 is not an assigned area code. The bioboostx case --
                                            # fabricated contact details, so nothing here is checkable
                    "biochemapi.com",       # "BiochemAPI" -- API is active pharmaceutical ingredient,
                                            # so the name says it supplies the raw material we supply.
                                            # The genscript and btbiolabs case: our side of the trade,
                                            # and the row states no US signal and no country
                    "dragon-pharma.com",    # "Dragon Pharma" is a widely known anabolic steroid label,
                                            # not a peptide business, and the row carries no US signal
                                            # and no stated country. Nothing in the catalog is aimed at
                                            # an AAS brand and the geography does not clear either
                    "peptidedropship.com",  # the harvested name is its own page title, "BPC-157
                                            # Wholesale Supplier with COAs | PeptideDropship" -- it
                                            # sells the pitch we sell, so it is a competitor, and the
                                            # only contact is a personal gmail (ahsanmilan080@)
                    "charlestonhealthspan.com",  # the harvested vendor name is not a name at all,
                                            # it is the title of the page we scraped: 'Why "Research
                                            # Peptides" Are a Dangerous Health Risk - Charleston ...'
                                            # A clinic publishing a warning against research peptides
                                            # is not a wholesale buyer of them
                    "bioboostx.com",        # harvested name is the link label "Visit BioBoostX ->",
                                            # no US signal, and both phones are impossible as US
                                            # lines: "(551) 040-0976" has an exchange starting with
                                            # 0, which NANP does not allow, and 590 is Guadeloupe,
                                            # not an assigned area code. Fabricated contact details
                    "henganpeptidefactory.com",  # "Hengan Peptide Factory", reachable only at an
                                            # outlook.com address, country not stated. A peptide
                                            # factory offering OEM supply is our side of the trade,
                                            # the btbiolabs and genscript case, not a buyer
                    "janoshiik.com",        # typo-variant of janoshiklab.com below (Janoshik
                                            # analytical lab, spelled with a doubled i); same lab,
                                            # same reason: it tests peptides, it does not buy them
                    "lumalexlaw.com",       # the domain says it: a law firm. It surfaced through a
                                            # directory listing, not a peptide business
                    "verifiedcreditcardprocessing.com",  # a payment processor -- the domain is
                                            # the business description. Not a peptide buyer
                    "janoshiklab.com",      # Janoshik is an analytical laboratory, not a vendor:
                                            # its catalogue is test panels priced in dollars ("Blind
                                            # common anabolic steroid screening -- oils 120 $"), and
                                            # it describes itself as "harm reduction chemical analysis".
                                            # It analyses other people's peptides; it does not buy any.
                                            # The crawler matched semaglutide, tirzepatide and
                                            # retatrutide because those are the assays it sells
                    "americanpeptide.co",   # not a vendor despite the name: the American Peptide
                                            # Association, a trade body selling $199/month memberships
                                            # with committees, a Scientific Advisory Board and "group
                                            # purchasing opportunities with vetted vendors". It
                                            # represents buyers, it does not buy
                    "empiremedicaltraining.com",  # "Empire Medical Training: Hands-On CME
                                            # Courses for Physicians, Nurses & Dentists", whose
                                            # Academy of Functional Medicine sells the "Most Complete
                                            # Peptide Course". It trains the clinicians who buy
                                            # peptides; it is not one of them
                    "mypeptideuniversity.com",  # "Peptide University | Peptide Therapy
                                            # Certification & Training" -- its About page calls it "an
                                            # academic-grade education platform for licensed
                                            # clinicians", with a faculty rather than a catalogue. It
                                            # teaches peptide therapy; it does not buy peptides
                    "biorootai.com",        # BioRoot AI: the homepage returns a <title> and no body
                                            # text at all -- a JS-only shell we cannot read -- and the
                                            # discovery row that produced it had already noted "reads
                                            # like editorial/affiliate site" with no phone and an
                                            # address scraped off the privacy page. Nothing here
                                            # establishes a business that buys peptides
                    "bioxcell.com",         # Bio X Cell manufactures monoclonal antibodies for in vivo
                                            # research out of New Hampshire. It is a real US company and
                                            # not foreign, but it makes antibodies, not peptides, and
                                            # has no use for wholesale peptide supply -- and the address
                                            # harvested for it, support@, is its website help desk
                    "rop-peptide.com",      # "a dedicated peptide manufacturer ... with in-house
                                            # laboratory capabilities ... from synthesis to final
                                            # delivery" -- it makes what we make, so it is upstream of
                                            # us rather than a buyer. Its US presentation is also
                                            # borrowed: the one customer testimonial, signed "Aviana
                                            # Plummer", ends "Will definitely reorder." with a Chinese
                                            # full-width period, so the review was typed on a Chinese
                                            # keyboard. No address, no email at the domain, and
                                            # WhatsApp and Telegram as the only channels
                    "usprimeway.com",       # no A record on apex or www: the address is at a dead
                                            # host, and the site it was harvested from is the Hong
                                            # Kong primewaypeptide.com above
                    "ilumapeptide.co",      # the domain no longer resolves at all -- no A record on
                                            # the apex or on www, while control domains answer fine.
                                            # The business is gone; a follow-up would only bounce
                    "pepty.app",            # "Peptide Price Comparison | Pepty" -- it ranks other
                                            # vendors' prices for shoppers and buys nothing itself
                    "ultimapharma.com"}     # sells "AAS, HGH, PEPTIDES": its menu is Injectable
                                            # Steroids, Oral Steroids, Peptides. Anabolic steroids are
                                            # Schedule III, and this is a direct-to-consumer storefront
                                            # for them, not a research peptide vendor. Outside the
                                            # audience the campaign is for, and not a name to have
                                            # Jonathan's pitch sitting next to.

# Both domain sets above are matched against the domain of the address we would
# write to, and for a business that publishes a Gmail or Outlook address that is
# "gmail.com" -- so a business excluded for any reason, geography included, slips
# past them when it is only reachable at freemail. These are the exact addresses.
# Not declined and not foreign -- simply unreadable today, so not written to.
# The campaign's rule is that we do not email a business whose site we cannot read,
# and that has to survive the contact already being in the queue: these entered
# before the site started refusing us. Kept separate from DECLINED_* so that a site
# coming back up is a one-line deletion rather than an argument about whether the
# business was ever rejected.
HELD_CONTACTS = {
    "customersupport@coffeeandpeppers.com":  # every path now returns one 54KB
        "\"coffeeandpeppers.com - Access\" gate page, so there is nothing to read; and the "
        "name is not evidence of a peptide business either way. It was emailed once before "
        "the gate went up",
    "testing@vanguardlaboratory.com":  # 403 on every page, so unreadable today, and
        "the address is testing@ and the name is Vanguard Laboratory -- most likely an "
        "analytical lab selling assays like janoshiklab.com, which is not a buyer. Held "
        "rather than declined because nothing could actually be read to confirm it",
    "pepwarehousecs@gmail.com":  # peptideswarehouse.com answers 403 to every HTML
        "site 403s every page; only robots.txt reads, and the stored row has no US "
        "signal and a gmail address, so nothing else vouches for it",
}

# A vendor whose own name calls it a peptide factory is offering OEM/raw-material
# supply -- the same side of the trade we are on, so there is nothing to sell it.
# The name is the evidence, which is why this is a pattern and not a domain list:
# "Hengan Peptide Factory" reaches us at outlook.com and "Peptide Factory CN" at a
# .com, so neither a domain nor a TLD rule would catch both.
FACTORY_NAME = re.compile(r"\bpeptides?\s+factory\b", re.I)

DECLINED_CONTACTS = {"pj91920107@icloud.com",  # the only address for hkburson.com (Hong Kong
                                            # Burson PolyPeptide RD Limited); on icloud.com, so
                                            # neither the domain entry nor the hk prefix reaches it
                     "ahsanmilan080@gmail.com",  # the only address for peptidedropship.com below;
                                            # on gmail, so the domain entry cannot reach it
                     "henganpeptidefactory@outlook.com",  # "Hengan Peptide Factory" -- an OEM
                                            # supplier, our side of the trade. FACTORY_NAME above
                                            # catches it from the name, but the address is on
                                            # outlook.com, so nothing catches it when the name is
                                            # absent -- which is exactly how the tripwire found it
                     "ukpeptidesupply99@gmail.com",  # the address itself says UK; \buk\b in
                                            # FOREIGN cannot see it inside a run-together local part,
                                            # and the queue held it under the name "Visit Site"
                     "regenwellph@gmail.com",  # Regenwell PH, Philippines (see regenwellph.com
                                            # above); on gmail, so the domain list cannot reach it
                     "support@adminnurapeptide.com",  # the host does not exist: no DNS record
                                            # at all, while nurapeptide.com resolves. Our harvester
                                            # glued "admin" onto the domain, the same fabrication as
                                            # info@www.revitalyzemd.com. The row's all_emails carries
                                            # the real support@ and wholesale@nurapeptide.com, so this
                                            # is a recoverable Florida vendor -- but swapping the
                                            # address here would be us choosing who to write to, and
                                            # that is Jonathan's to do deliberately, not ours in
                                            # passing. Flagged in reply_notes.csv
                     "yidanbiotech@gmail.com",  # Shanghai Yidan Biotechnology (see yidanbio.com
                                            # above); on gmail, so the domain list cannot reach it
"thepeptidecatalog@gmail.com",  # price-comparison directory, not a supplier
                     "sec9vzion@outlook.com",        # peptidedosages.com, a dosing-chart site
                     "beatyjin51@gmail.com",         # healtlab.com, contactable only on +852 Hong Kong
                     "wyi556911@gmail.com",         # yansenpeptidesfactory.com, Shenzhen, China
                     "peptpedia@gmail.com",         # peptpedia.org, a peptide encyclopedia
                     "productsmax16@gmail.com",     # arizona-mall.com: an American-sounding domain
                                                    # over "GMP Factory OEM Supply ... for Global Labs
                                                    # and Manufacturers" -- upstream of us, not a buyer
                     "hechuan1022@gmail.com",       # peptidechn.com, ships out of Hong Kong
                     "fuguo20250808@gmail.com",     # purepeptide99.com, Wan Chai, Hong Kong
                     "godbiolab@pm.me",             # godbiolab.is sells "GODTROPIN (HGH) 240IU" and HCG
                                                    # direct to consumers, publishes no address, phone
                                                    # or country, and says at the top of every page
                                                    # that its previous domain "was suspended".
                                                    # Distributing hGH for non-approved uses is a
                                                    # federal offence; this is not an RUO vendor
                     "dzseszikajerbe@gmail.com",    # aotaipeptide.com, a Guangzhou peptide factory
                     "alex.barn001@gmail.com",      # barnpeptides.com dresses itself up as American --
                                                    # "Business Hours Mon-Fri: 9AM-6PM EST" and a +1
                                                    # (914) number -- but that number is its Telegram
                                                    # handle, its WhatsApp is +852 5390 6568 (Hong
                                                    # Kong), and its only email is this gmail address
                     "landgb39@gmail.com",          # rop-peptide.com, a synthesis house (see above)
                     "chloesong1008@gmail.com",     # weipeptide.com, a Hong Kong factory that lists six
                                                    # gmail addresses for six named sales reps
                     "changyuanpolypeptide@outlook.com",  # changyuanpolypeptide.com, Shenzhen
                     "gzhaiyitong@outlook.com",     # zghiyit.com, two (852) Hong Kong numbers; the
                                                    # local part is "gz" for Guangzhou and the domain
                                                    # "zg" for Zhongguo. The stored state reads
                                                    # "Georgia", which is where the GA came from
                     "vivpeptide@gmail.com",        # vivpeptide.com, three (852) Hong Kong numbers
                     "sulanpeptides01@gmail.com",   # sulanpeptides.com, a Hong Kong factory
                     "thepeptideco@proton.me"}      # thepeptideco.shop, an Australian storefront --
                                                    # proton.me is freemail, so the domain sets above
                                                    # cannot see it either


FREEMAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "proton.me", "protonmail.com", "pm.me", "tuta.com",
            "tutanota.com", "qq.com", "163.com", "icloud.com", "aol.com", "sudomail.com", "live.com", "msn.com"}
# Consumer mail services essentially nobody outside China uses. A US peptide brand
# does not take its wholesale enquiries at a QQ number: norcopeptide.com published
# 2546077119@qq.com and dressed the same digits up as the US phone "(254) 607-7119".
FOREIGN_FREEMAIL = {"qq.com", "163.com", "126.com", "sina.com", "sina.cn", "yeah.net",
                    "foxmail.com", "aliyun.com", "139.com", "189.cn"}
GENERIC_NAMES = {"your practice", "your business"}


# Bases too generic to identify a business. peptide.partners and peptides.com are
# different companies, so collapsing both to "peptide" would silence one of them.
GENERIC_BRANDS = {"peptide", "peptides", "lab", "labs", "bio", "research", "gmail", "shop", "store"}


def brand_key(domain):
    """Collapse a domain to the brand behind it, or "" when that cannot be told.

    A scraped queue carries one business under several hosts: arizonapeptides.us
    and arizonapeptidesus.com are one operator, as are ms-peptides.com and
    mspeptides.com. Emailing both is emailing one business twice. Drop the TLD,
    the punctuation and a trailing "us"/"usa" that only marks the country.
    """
    base = (domain or "").lower().split(":")[0]
    if base.startswith("www."):
        base = base[4:]
    base = re.sub(r"[^a-z0-9]", "", base.split(".")[0])
    base = re.sub(r"(usa|us)$", "", base)
    if len(base) < 6 or base in GENERIC_BRANDS:
        return ""
    return base


def contact_key(email, domain=""):
    """What counts as "the same business" when deduplicating contacts.

    One contact per domain is right for a company mailbox, but freemail is not a
    company. Three clinics that publish a Gmail address are three businesses, and
    keying them all on gmail.com meant the first one emailed locked out every
    later one for good -- silently, because they simply stopped appearing in the
    queue. Freemail contacts are therefore keyed on the address itself.
    """
    e = (email or "").strip().lower()
    d = (domain or "").strip().lower() or (e.split("@", 1)[1] if "@" in e else "")
    if d in FREEMAIL:
        return "addr:" + e
    return d


def is_foreign(domain, name=""):
    d = (domain or "").lower()
    # str.lstrip takes a SET of characters, not a prefix: "walkerchemicals.store"
    # .lstrip("www.") is "alkerchemicals.store", so every domain starting with w
    # missed this list entirely.
    bare = d[4:] if d.startswith("www.") else d
    if bare in FOREIGN_DOMAINS:
        return True
    if FOREIGN_IN_DOMAIN.search(d) or FOREIGN_DOMAIN_PREFIX.match(d):
        return True
    return bool(FOREIGN.search(domain) or FOREIGN_NAME.search(name or ""))


# Words that say nothing about WHICH business this is, so they cannot vouch for a
# scraped name on their own.
NAME_STOPWORDS = {"the", "and", "for", "peptide", "peptides", "research", "lab", "labs",
                  "bio", "biotech", "biotechnology", "inc", "llc", "co", "company",
                  "group", "usa", "shop", "store", "online", "buy", "best", "premium"}


def name_matches_domain(name, domain):
    """Does this scraped name plausibly belong to this domain?

    Scrapers pick up whatever a page happens to say, so a title can name a
    different business entirely -- warehousepeptides.com came back as "The Peptide
    Lab". Telling somebody "I came across The Peptide Lab" when they are not that
    company reads worse than naming their domain, so a name is only trusted when
    some distinctive word in it also appears in the domain.
    """
    d = (domain or "").lower()
    words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower())
             if len(w) >= 4 and w not in NAME_STOPWORDS]
    if not words:
        return False
    return any(w in d for w in words)


# Words that carry no identity on their own. A name made only of these is a page
# title, not a business: "BULK Supply" is the first segment of "BULK Supply - AOD
# 9604 5mg - Regenerate Peptides", and "New" is what is left of "New-U".
GENERIC_WORDS = set("""
    high low new free fast bulk quality premium official trusted reliable verified
    tested pure safe secure online order orders product products research lab labs
    login account cart menu search sale sales deal deals price prices pricing supply
    supplies shop store home welcome peptide peptides best top usa us buy wholesale
    the a an and of for your our inc llc co company group
""".split())


def usable_name(n):
    """Is this something we can put in front of a stranger in a subject line?

    A freemail host is not a business name; neither is a stub like "New" left over
    from a truncated page title, nor a phrase whose every word is generic.
    """
    n = (n or "").strip()
    if not n or len(n) < 4 or n.lower() in FREEMAIL:
        return False
    words = [w for w in re.split(r"[^A-Za-z0-9']+", n.lower()) if w]
    return bool(words) and not all(w in GENERIC_WORDS for w in words)


def clean_vendor(name, domain):
    # Names come out of page titles, so they arrive HTML-escaped: "Charleston
    # Men&#x27;s Clinic", "Vital Force Therapy &amp; Wellness". Unescaped, that
    # is what the recipient reads in the subject line of a cold email.
    n = html.unescape(name or "").strip()
    # A "name" that is an address is a harvest artifact, not a business: the
    # contact page for universalbiolabs.com yielded "\u2709\ufe0f support@
    # universalbiolabs.com", which would have gone out as the subject line of a
    # cold email. Leading pictographs are the same artifact, picked up off a
    # decorated heading. Neither is worth repairing -- the domain is a true name
    # for the business and the template already uses it for most rows.
    n = re.sub(r"^[^\w(]+", "", n).strip()
    if "@" in n:
        return domain
    if not n or len(n) < 3 or len(n.split()) > 5 or JUNK_VENDOR.search(n):
        return domain
    if not name_matches_domain(n, domain):
        return domain
    # A scraped "name" that is itself a hostname is no better than the domain we
    # are writing to, and when the two disagree (regentide.net stored against
    # contact@regentide.com) the mismatch is visible in the subject line.
    if " " not in n and "." in n and n.lower().rstrip("/") != (domain or "").lower():
        return domain
    return n
LEGACY_SENT_AT = "2026-09-14T16:00:00Z"                    # all pre-schema sends went out on 2026-09-14

COLS = ["email", "domain", "audience", "mode", "via", "sent_at", "last_touch_at", "stage", "status"]
STAGE_LINES = {
    1: "Just floating this back to the top of your inbox.",
    2: "Checking in once more in case this got buried.",
    3: "Last note from me on this -- I don't want to clutter your inbox.",
}
DEFAULT_SENDER = {
    "SENDER_NAME": "Jonathan Cole",
    "SENDER_EMAIL": "jonathan@marinexisbiologics.com",
    "SENDER_COMPANY": "Marinexis Biologics",
    "SENDER_POSTAL_ADDRESS": "6671 S Las Vegas Blvd, Las Vegas, NV 89118",
}
UNSUB_LINE = "Don't want to hear from us again? Reply with \"unsubscribe\" and we'll remove you."


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def sender():
    d = dict(DEFAULT_SENDER)
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in d and v:
                    d[k] = v
    return d


# ---------- state ----------
def load_sent():
    rows = []
    if SENT.exists():
        with open(SENT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append({k: (r.get(k) or "").strip() for k in COLS})
    for r in rows:                                   # tolerate the pre-follow-up schema
        r["email"] = r["email"].lower()
        r["domain"] = r["domain"] or r["email"].split("@", 1)[1]
        r["mode"] = r["mode"] or "sent"
        r["sent_at"] = r["sent_at"] or LEGACY_SENT_AT
        r["last_touch_at"] = r["last_touch_at"] or r["sent_at"]
        r["stage"] = r["stage"] or "0"
        r["status"] = r["status"] or "active"
    return rows


def save_sent(rows):
    with open(SENT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader(); w.writerows(rows)


def load_queue():
    with open(QUEUE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_draft_ids():
    m = {}
    if DRAFT_IDS.exists():
        with open(DRAFT_IDS, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                m[r["email"].strip().lower()] = r["draft_id"].strip()
    return m


def local_day(ts_iso):
    """Calendar day in Pacific time (Jonathan's day), so the 100/day cap resets at local midnight, not UTC."""
    from zoneinfo import ZoneInfo
    return parse(ts_iso).astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def today_count(rows):
    t = local_day(iso(now()))
    return sum(1 for r in rows if local_day(r["last_touch_at"]) == t)


# ---------- rendering ----------
def peps_of(qrow):
    if qrow and qrow.get("audience") == "medspa":
        m = re.search(r"and saw you offer (.*?) -- that's exactly", qrow["body"], re.S)
        if m:
            return m.group(1).replace("\n", " ")
    return "peptides"


def orig_subject(aud, name):
    tail = "US-made peptide supply for your practice" if aud == "medspa" else "US-made peptide supply, wholesale"
    return tail if name in GENERIC_NAMES else f"{name} - {tail}"


def render_fu(aud, name, peps, stage, sd):
    text = FU_TPL[aud].read_text(encoding="utf-8")
    first, _, rest = text.partition("\n")
    subject_t = first.split(":", 1)[1].strip() if first.lower().startswith("subject:") else "Re: {orig_subject}"
    body_t = rest.lstrip("\n").rstrip("\n")
    subs = {
        "{orig_subject}": orig_subject(aud, name), "{business_name}": name, "{peptides}": peps,
        "{stage_line}": STAGE_LINES.get(stage, STAGE_LINES[max(STAGE_LINES)]),
        "{sender_email}": sd["SENDER_EMAIL"], "{sender_company}": sd["SENDER_COMPANY"],
        "{sender_postal_address}": sd["SENDER_POSTAL_ADDRESS"], "{unsubscribe_line}": UNSUB_LINE,
        "{sender_name}": sd["SENDER_NAME"],
    }
    for k, v in subs.items():
        subject_t = subject_t.replace(k, v); body_t = body_t.replace(k, v)
    return subject_t, body_t


# ---------- selection ----------
def third_party():
    """Addresses that belong to a different business than the site they came from.

    The crawler records every address on a vendor's pages, and some are somebody
    else's: lgipeptides.com publishes its marketing agency's, scientificamerican.com
    its publisher's, and a few sites publish placeholders like jane.smith@clinic.com.
    Built by outreach/audit_third_party.py; see that file for how the call is made.
    """
    global _THIRD_PARTY
    if _THIRD_PARTY is None:
        path = OUT / "third_party_contacts.csv"
        if not path.exists():
            _THIRD_PARTY = set()
        else:
            with open(path, newline="", encoding="utf-8") as f:
                _THIRD_PARTY = {r["email"].strip().lower() for r in csv.DictReader(f) if r.get("email")}
    return _THIRD_PARTY


def initial_candidates(sent_rows):
    done = set()
    for r in sent_rows:
        done.add(r["email"]); done.add(contact_key(r["email"], r["domain"]))
    cands = []
    for r in load_queue():
        e = r["email"].strip().lower()
        d = e.split("@", 1)[1]
        if e in done or contact_key(e) in done:
            continue
        if skip_contact(e, r["audience"], d, r.get("business_name", "")):
            continue
        cands.append(r)
    dids = load_draft_ids()                                       # file order = top of the Drafts folder first
    by_email = {r["email"].strip().lower(): r for r in cands}
    drafted = [by_email[e] for e in dids if e in by_email]
    rest = [r for r in cands if r["email"].strip().lower() not in dids]
    vendors = [r for r in rest if r["audience"] == "vendor"]
    medspas = [r for r in rest if r["audience"] == "medspa"]
    ordered = vendors + medspas                                   # after the drafts: vendors first, then med spas
    pri = [r for r in ordered if r["email"].split("@", 1)[1] in PRIORITY_DOMAINS]
    ordered = drafted + pri + [r for r in ordered if r not in pri]
    out, seen, brands = [], set(), {brand_key(r["domain"]) for r in sent_rows} - {""}
    for r in ordered:                                             # one contact per domain per campaign
        d = contact_key(r["email"])
        if d in seen:
            continue
        b = brand_key(d)
        if b and b in brands:                                     # ...and one per brand behind the domain
            continue
        seen.add(d)
        if b:
            brands.add(b)
        out.append(r)
    return out


def skip_contact(email, audience="", domain="", name=""):
    """Should this contact be left alone, whatever stage it is at?

    The one place these rules live. initial_candidates() used to carry its own
    copy of them, which is how a fix could land on follow-ups and silently miss
    first contact -- the www. rule below was written, the batch rebuilt, and
    info@www.revitalyzemd.com came back anyway.
    """
    d = (domain or (email.split("@", 1)[1] if "@" in email else "")).lower()
    # A harvesting artifact: the crawler kept the "www." off the page's own URL
    # and built "info@www.revitalyzemd.com". Mail to that host does not exist.
    # The real address is almost certainly info@revitalyzemd.com, but almost
    # certainly is a guess, and a guessed address is exactly what this campaign
    # does not send -- so the contact is dropped rather than repaired.
    if d.startswith("www."):
        return True
    # The same class, on the other side of the @: "%20melanie@..." and
    # "u00a0info@..." are a URL-encoded space and a non-breaking space that came
    # out of a mailto: href. ingest_resolved normalises those away now, but
    # anything still carrying a percent-escape or whitespace is not a readable
    # address and must not be written to.
    if "%" in email or re.search(r"\s", email):
        return True
    # A third shape of the same artifact, this time in the host: the harvester
    # produced "info@pathwayhealthnwellness..com" for a Mesa clinic. A doubled,
    # leading or trailing dot is not a hostname that resolves, and the obvious
    # repair -- delete one dot -- is still a guess at an address nobody has read.
    if ".." in d or d.startswith(".") or d.endswith(".") or "." not in d:
        return True
    if d in DECLINED_DOMAINS or email in DECLINED_CONTACTS or email in third_party():
        return True
    if email in HELD_CONTACTS or d in HELD_CONTACTS:
        return True
    if FACTORY_NAME.search(name or ""):
        return True
    if FOREIGN_SUFFIX.search((name or "").strip()):
        return True
    if audience == "vendor" and (is_foreign(d, name) or d in FOREIGN_FREEMAIL):
        return True
    return email.split("@", 1)[0] in FOREIGN_LOCAL


def due_followups(sent_rows):
    if now() < parse(FOLLOWUP_START):
        return []
    cutoff = now() - timedelta(days=FOLLOWUP_DAYS)
    # The same gates that decide who we start writing to decide who we keep
    # writing to. A vendor only shown to be foreign after its first email --
    # hkroids.com publishes a +86 number and four +852 ones -- was still queued
    # for follow-ups, because the filters only ran over new candidates.
    due = [r for r in sent_rows
           if r["status"] == "active" and int(r["stage"]) < MAX_FOLLOWUPS and parse(r["last_touch_at"]) <= cutoff
           and not skip_contact(r["email"].strip().lower(), r.get("audience", ""), r.get("domain", ""),
                                r.get("business_name", ""))]
    due.sort(key=lambda r: r["last_touch_at"])
    return due


# ---------- commands ----------
def check_exclusions():
    """Every address we have promised not to write to must still be refused.

    These lists are edited by hand, one entry per wave, and a bad edit is silent:
    on Sep 20 a new DECLINED_CONTACTS entry was pasted over the top of
    yidanbiotech@gmail.com -- a Shanghai steroid vendor blocked an hour earlier --
    and it reappeared in the very next batch. Nothing failed; the set was simply
    one member smaller. So before a wave is built, assert that each listed address
    is actually refused, and stop rather than build a batch on a broken list.
    """
    want = []
    try:
        for line in open(EXPECTED, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                want.append(line)
    except FileNotFoundError:
        sys.exit(f"{EXPECTED} is missing -- it is the only copy that can catch a "
                 "deleted exclusion, so a wave must not be built without it")
    bad = [e for e in want
           if not skip_contact(e if "@" in e else "someone@" + e, "vendor",
                               e.split("@", 1)[1] if "@" in e else e, "")]
    if bad:
        sys.exit(f"{len(bad)} exclusion(s) listed in {EXPECTED} are no longer refused: "
                 + ", ".join(bad[:8]) + ("..." if len(bad) > 8 else ""))


def cmd_next(n, out_json):
    check_exclusions()
    rows = load_sent(); qi = {r["email"].strip().lower(): r for r in load_queue()}
    dids = load_draft_ids(); sd = sender()
    budget = max(0, DAILY_CAP - today_count(rows)); n = min(n, budget, HOURLY_CAP)
    items = []
    fu_cap = int(n * FU_SHARE + 0.999) if n else 0
    for r in due_followups(rows):
        if len(items) >= fu_cap:
            break
        qrow = qi.get(r["email"]); aud = r["audience"] if r["audience"] in FU_TPL else "vendor"
        name = (qrow or {}).get("business_name") or r["domain"]; peps = peps_of(qrow); stage = int(r["stage"]) + 1
        if aud == "vendor":
            name = clean_vendor(name, r["domain"])
        if name == r["domain"] and r["domain"] in FREEMAIL:      # a freemail domain is not a business name
            name = "your practice" if aud == "medspa" else "your business"
        subj, body = render_fu(aud, name, peps, stage, sd)
        items.append({"kind": "fu", "stage": stage, "audience": aud, "to": r["email"], "name": name,
                      "peps": peps if aud == "medspa" else "-", "subject": subj, "body": body, "draftId": ""})
    for r in initial_candidates(rows):
        if len(items) >= n:
            break
        e = r["email"].strip().lower()
        name, subj, body, did = r["business_name"], r["subject"], r["body"], dids.get(e, "")
        em_dom = e.split("@", 1)[1]
        if r["audience"] == "vendor":
            # clean_vendor checks the name against the domain we are writing to.
            # For a contact who publishes a Gmail or Outlook address that check is
            # meaningless -- the name will never match "gmail.com" -- and the
            # fallback made the subject line read "gmail.com - US-made peptide
            # supply, wholesale". The queued name was already checked against the
            # business's own site at ingest, so for freemail it stands as it is.
            new = name if em_dom in FREEMAIL else clean_vendor(name, em_dom)
            if not usable_name(new):
                new = "your business"
            if new != name:
                subj = orig_subject("vendor", new)
                body = body.replace(f"I came across {name}\n", f"I came across {new}\n", 1)
                name, did = new, ""                              # never send a draft whose name we rewrote
        items.append({"kind": "initial", "stage": 0, "audience": r["audience"], "to": e, "name": name,
                      "peps": peps_of(r) if r["audience"] == "medspa" else "-", "subject": subj,
                      "body": body, "draftId": did})
    Path(out_json).write_text(json.dumps(items), encoding="utf-8")
    fu = sum(1 for i in items if i["kind"] == "fu"); ini = len(items) - fu
    print(f"BATCH {len(items)}  fu={fu} initial={ini}  (budget_left_today={budget}, cap={DAILY_CAP})")
    bad = [i["to"] for i in items if "{" in i["subject"] or "{" in i["body"]]
    if bad:
        print("!!! PLACEHOLDER LEFTOVERS:", bad)
    for i, it in enumerate(items, 1):
        kind = f"FU{it['stage']}" if it["kind"] == "fu" else "INIT"
        aud = "MS" if it["audience"] == "medspa" else "VN"
        print(f"{i}\t{kind}\t{aud}\t{it['to']}\t{it['name']}\t{it['peps']}\t{it['draftId'] or '-'}")


def cmd_record(batch_json, upto=None, skip=()):
    items = json.loads(Path(batch_json).read_text(encoding="utf-8"))
    rows = load_sent(); idx = {r["email"]: r for r in rows}; t = iso(now()); via = sender()["SENDER_EMAIL"]
    n_new = n_fu = 0
    for i, it in enumerate(items, 1):
        if upto and i > upto:
            break
        if i in skip:
            continue
        e = it["to"].strip().lower()
        if it["kind"] == "initial":
            if e in idx:
                continue                                          # idempotent
            row = {"email": e, "domain": e.split("@", 1)[1], "audience": it["audience"], "mode": "sent", "via": via,
                   "sent_at": t, "last_touch_at": t, "stage": "0", "status": "active"}
            rows.append(row); idx[e] = row; n_new += 1
        else:
            r = idx.get(e)
            if r and int(r["stage"]) < int(it["stage"]):
                r["stage"] = str(it["stage"]); r["last_touch_at"] = t; n_fu += 1
    save_sent(rows)
    print(f"recorded initial={n_new} followups={n_fu}  (rows 1..{upto or len(items)}, skipped {sorted(skip) or 'none'})")


def cmd_mark(status, path):
    assert status in ("replied", "bounced", "unsubscribed", "active"), status
    keys = {k.strip().lower().lstrip("<").rstrip(">") for k in Path(path).read_text(encoding="utf-8").split() if k.strip()}
    rows = load_sent(); n = 0
    for r in rows:
        if (r["email"] in keys or r["domain"] in keys) and r["status"] != status:
            r["status"] = status; n += 1
    save_sent(rows)
    print(f"marked {n} as {status}")


def cmd_export():
    """Write category lists: contacted (everyone emailed), replied, bounced, and the not-yet-emailed queue."""
    rows = load_sent()
    def dump(name, sel, cols):
        with open(OUT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
            for r in sel: w.writerow({k: r.get(k, "") for k in cols})
        return len(sel)
    base = ["email", "domain", "audience", "sent_at", "last_touch_at", "stage", "status"]
    n1 = dump("contacted.csv", [r for r in rows if r["status"] in ("active", "manual")], base)
    n2 = dump("replied.csv", [r for r in rows if r["status"] == "replied"], base)
    n3 = dump("bounced.csv", [r for r in rows if r["status"] == "bounced"], base)
    pend = initial_candidates(rows)
    n4 = dump("not_yet_emailed.csv", [{"email": r["email"], "domain": r["email"].split("@", 1)[1], "audience": r["audience"],
                                        "business_name": r["business_name"]} for r in pend],
              ["email", "domain", "audience", "business_name"])
    print(f"exported contacted={n1} replied={n2} bounced={n3} not_yet_emailed={n4}")


def cmd_stats():
    rows = load_sent()
    from collections import Counter
    st = Counter(r["status"] for r in rows)
    sg = Counter(r["stage"] for r in rows if r["status"] == "active")
    pend = len(initial_candidates(rows)); due = len(due_followups(rows)); tc = today_count(rows)
    active = [r for r in rows if r["status"] == "active" and int(r["stage"]) < MAX_FOLLOWUPS]
    nxt = min((parse(r["last_touch_at"]) + timedelta(days=FOLLOWUP_DAYS) for r in active), default=None)
    print(f"sent_total={len(rows)} status={dict(st)} active_by_stage={dict(sorted(sg.items()))}")
    print(f"today_sent={tc} budget_left_today={max(0, DAILY_CAP - tc)} cap={DAILY_CAP}")
    print(f"initial_pending={pend} followups_due_now={due} next_followup_due={iso(nxt) if nxt else '-'}")
    print("campaign=" + ("complete" if (pend == 0 and not active) else "running"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "stats":
        cmd_stats()
    elif a[0] == "migrate":
        save_sent(load_sent()); print("migrated", SENT)
    elif a[0] == "next":
        cmd_next(int(a[1]) if len(a) > 1 else 100, a[2] if len(a) > 2 else str(OUT.parent / ".send_batch.json"))
    elif a[0] == "record":
        upto = None; skip = set(); rest = a[2:]
        i = 0
        while i < len(rest):
            if rest[i] == "--skip":
                skip = {int(x) for x in rest[i + 1].split(",") if x.strip()}; i += 2
            else:
                upto = int(rest[i]); i += 1
        cmd_record(a[1], upto, skip)
    elif a[0] == "mark":
        cmd_mark(a[1], a[2])
    elif a[0] == "export":
        cmd_export()
    else:
        sys.exit("usage: serve_send.py [stats | migrate | next N out.json | record batch.json [UPTO] [--skip i,j] | mark STATUS file]")
