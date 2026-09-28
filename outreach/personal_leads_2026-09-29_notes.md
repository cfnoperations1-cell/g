# Personal leads, 2026-09-29: notes

File: `outreach/personal_leads_2026-09-29.csv`. It has 101 rows, one person per business, and each row holds one written email. Nothing has been sent or drafted in Gmail. `draft_queue.csv`, `sent_log.csv` and `serve_send.py` are unchanged.

## How the list was built
- **Pool.** The pool was 121 RUO vendors and 429 telehealth businesses from the 2026-09-28 hunts. None of their domains or addresses is in `sent_log.csv`, and all of them pass `serve_send.skip_contact()`. I rechecked both conditions for every final row, including direct addresses on other domains.
- **Crawl.** I crawled every site: the home, about, team, contact, legal and wholesale pages. For the finalists I also crawled peptide, service and product pages found through each sitemap. The crawl used normal TLS verification through the environment proxy.
- **Ranking.** Rows are ordered by visible evidence, in this order:
  1. Size: published patient or member counts, multi-state or all-50-state coverage, number of locations and years operating.
  2. Fit: how many compounds on our list they visibly carry.
  3. Directness of the address.

  Large national platforms with broad GLP-1 and peptide menus come first. After them come multi-location clinics with deep peptide menus, then single clinics, then GLP-1-only practices.
- **People.** I recorded a person only when their own site names them with a role: owner, founder, CEO, president, COO or medical director.
- **Addresses.** Every address is copied from the business's own pages. I did not guess, build or pattern-match any address. `evidence_url` gives the page where the address is published, then `| person:` and the page that names the person and role.
- **Specifics in the emails.** Any compound an email says the prospect carries appears on the prospect's own pages in my crawl. Compounds I could not confirm there are worded as what Marinexis supplies. The offer names only compounds on your list.
- **Claims left out.** The copy makes no claims about manufacturing, Made in USA, four labs, MOQ, price, lead time, FDA status or compounding. No brand-name drugs appear.

## Counts
| | direct_published | general_published | total |
|---|---|---|---|
| telehealth | 19 | 77 | 96 |
| vendor (RUO) | 3 | 2 | 5 |
| **total** | **22** | **79** | **101** |

Only 5 of the 121 RUO vendors name any person with a role on their site. The other 116 stay in the general pool.

## Person found, but no published direct address (sent to the general address, greeting uses their name)
These are all 79 `general_published` rows. They include HFW Longevity (Scott M. Davis, COO, product & sourcing), Ivologist (Dr. Utkal Patel), Telezen MD (Eric Viner), Maximus (Dr. Cameron Sepah), PeterMD (Bryan Henry), Male Excel (Craig Larsen), Lavender Sky (Dr. Eubanks), MEDVi (Matthew Gallagher), TKO (Joey Gilbert), Marek Health (Derek), Nu Image (Andreas Dettlaff), Joi + Blokes (Josh Whalen), Affinity (Brian Zeid), Options Medical (Jeremy Castle), Regenerative Revival (Dr. Sean Arora), Elite Health HRT (Tom Houston), PRIME Medical (Ryne San Hamel), The Biostation (Ross Bloom), Beverly Hills Concierge Doctor (Dr. Ehsan Ali), PeptidePure (Scott Mortensen) and Core Research Peptides (Sean). The full list is the `general_published` filter in the CSV.

## Needs your review before sending
1. **63 of the 101 businesses are already in `draft_queue.csv`** with the older generic draft ("Hi there" / "US-made"). Rows affected: 1, 3, 4, 7, 8, 10, 11, 14, 16, 17, 19, 21, 22, 24, 25, 28, 29, 30, 32, 34, 37, 41–49, 51, 57, 59, 60, 62–66, 70–72, 74, 77–79, 82–87, 90–98, 100, 101.
   - Only one contact per business is allowed, so send either the personal email or the queued one, never both.
   - I did not edit the queue. Remove the queued rows for any personal emails you approve.
2. **Telehealth sourcing.** Several telehealth sites say they prescribe through licensed 503A/503B or "FDA-regulated" pharmacies, for example TKO, Ivologist and Telezen. They may not buy from a wholesaler directly. Decide whether this audience fits before sending. The copy makes no regulatory claims either way.
3. **Greeting exception.** Row 10, Lavender Sky, opens "Hi Dr. Eubanks," because the site gives no first name.
4. **Named contact, but ownership not explicit on the site.** Each is marked in `role`. Check them if you want owners only:
   - P3 Labs: Logan, the named inventory contact.
   - R&R Rejuvenation: Dr. Richard Kim.
   - Sana Vida: Dr. P. John Schanen.
   - 365 Weight Loss: Jodie Guardi. Her address is on seizetheday365health.com, published on the practice site.
   - Thrive Medical: Alison Brady.
   - Coastal Healthcare: Kirsten Lamb.
   - MetroMed: Gloria Bird.
   - Highland Longevity: Dr. Joshua Lindsley, medical director.
   - Stamina Center: Dr. Dwight Davis.
   - Dr. Neil Paulvin.
5. **Freemail addresses the business itself publishes.** These are the business's published contact:
   - pwrpeptidessupport@gmail.com (PWR lists it under "Joe, Owner / Wholesale Contact")
   - vestaaesthetics@gmail.com
   - badgerstatehydrate@gmail.com
   - michaelazizmd.staff@gmail.com
   - padgettmedicalocala@live.com
6. **Local leads.** Long Life Med (Las Vegas, row 28) and TKO (Reno, row 12) mention being nearby.

## Skipped on purpose
- **Address found only in metadata, not published for business contact:**
  - andyliu93ny@gmail.com (WordPress author field, peptideskingdom.com)
  - danishaslam1123@gmail.com (page-author field, purecompoundinglabs.com)
  - mikejarboe@gmail.com (verachainlabs.com)
  - will@medicalhealthinstitute.com (privacy contact; role unknown, and the named founder is Miguel Bertonatti)
- **Person's role not stated:**
  - Frontier Amino Labs (daniel@ is the only contact)
  - Biomod Peptides, Las Vegas (chris@ appears only in the site's schema)
- **Same person or same business twice:**
  - Ozari Health: Dr. Sean Arora is already contacted through Regenerative Revival.
  - fountain.net and fountaintrt.com: one business, and it offers testosterone only.
- **Weak fit**, meaning testosterone-only, no compounds from our list visible, or non-peptide care: Hone, The HRT Club, Wild Health, HOOT HRT, Elevate Health HRT, Eternal Vitality, Xena Health, InVita, Accelerate Wellness, and the fertility and sleep clinics.
- **Very large national brands:** Ro, LifeMD, Henry, Midi, Alloy, Evernow, Winona, Mira. A cold email is unlikely to reach a decision-maker, and they buy through pharmacy networks.
