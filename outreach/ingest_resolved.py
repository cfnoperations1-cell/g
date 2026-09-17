"""Add resolved PeptideBase vendors to the outreach queue.

Renders each row with the same vendor template the campaign already uses, then
applies the campaign's own gates before anything is appended:
  - a real published email, on a domain we can name
  - not foreign (FOREIGN / FOREIGN_NAME / FOREIGN_IN_DOMAIN / FOREIGN_DOMAINS)
  - not already emailed, queued, or a second domain for a business we know
Dry run by default; pass --apply to write.
"""
import csv, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_send as ss

ROOT = str(Path(__file__).resolve().parent.parent) + '/'
SCRATCH = os.environ.get('RESOLVED_DIR', str(Path(__file__).resolve().parent.parent / 'scraper')) + '/'
TPL = ROOT + 'emailer/message_vendor.txt'
QUEUE = ROOT + 'outreach/draft_queue.csv'
COLS = ['audience', 'email', 'business_name', 'to', 'subject', 'body']
# addresses that exist on pages but are never a person who reads mail
# Addresses read from a search engine's copy of a page are fine when the live page
# still exists, but peptidetech.co/contact-us now 404s, so cs@peptidetech.co may
# be an address the vendor has already retired. Held back until someone can read
# it off the live site.
HOLD = {'peptidetech.co': 'contact page that published the address now 404s'}

BAD_LOCAL = re.compile(r'^(no-?reply|donotreply|postmaster|abuse|webmaster|example|privacy|dmca|'
                       r'sentry|wixpress|squarespace|shopify|godaddy|cloudflare)@', re.I)


def render(name):
    raw = open(TPL, encoding='utf-8').read()
    first, rest = raw.split('\n', 1)
    subject = first.split(':', 1)[1].strip()
    body = rest.lstrip('\n').rstrip('\n')
    sd = ss.DEFAULT_SENDER
    sub = {'{business_name}': name,
           '{sender_email}': sd['SENDER_EMAIL'],
           '{sender_company}': sd['SENDER_COMPANY'],
           '{sender_postal_address}': sd['SENDER_POSTAL_ADDRESS'],
           '{unsubscribe_line}': ss.UNSUB_LINE}
    for k, v in sub.items():
        subject = subject.replace(k, v)
        body = body.replace(k, v)
    return subject, body


def known():
    bases, emails = set(), set()
    for f in ('outreach/sent_log.csv', 'outreach/not_yet_emailed.csv'):
        for r in csv.DictReader(open(ROOT + f, newline='', encoding='utf-8')):
            e = (r.get('email') or '').strip().lower()
            if e:
                emails.add(e)
                bases.add(ss.brand_key(e.split('@', 1)[1]))
            d = (r.get('domain') or '').strip().lower()
            if d:
                bases.add(ss.brand_key(d))
    for r in csv.DictReader(open(QUEUE, newline='', encoding='utf-8')):
        e = (r.get('email') or '').strip().lower()
        if e:
            emails.add(e)
            bases.add(ss.brand_key(e.split('@', 1)[1]))
    bases.discard('')
    return bases, emails


def main(apply_it):
    bases, emails = known()
    rows, seen = [], set()
    drops = {}
    for f in (os.environ.get('RESOLVED_FILES') or 'peptidebase_resolved_2026-09-17.csv').split(','):
        try:
            src = list(csv.DictReader(open(SCRATCH + f, newline='', encoding='utf-8')))
        except FileNotFoundError:
            print(f'(missing {f} -- skipping)')
            continue
        for r in src:
            vendor = (r.get('vendor') or '').strip()
            dom = (r.get('domain') or '').strip().lower().lstrip('.')
            em = (r.get('email') or '').strip().lower()
            st = (r.get('status') or '').strip().lower()

            def drop(why):
                drops.setdefault(why, []).append(vendor or dom or em)

            if st != 'ok' or not em or '@' not in em or not dom:
                drop('no usable email'); continue
            if em.split('@', 1)[1] != dom and dom not in em.split('@', 1)[1]:
                drop('email not on the named domain'); continue
            if BAD_LOCAL.match(em):
                drop('not a real inbox'); continue
            if em.split('@', 1)[0] in ss.FOREIGN_LOCAL:
                drop('non-English role address'); continue
            if getattr(ss, 'DECLINED_DOMAINS', set()) and dom in ss.DECLINED_DOMAINS:
                drop('on the declined list'); continue
            if dom in HOLD:
                drop('held: ' + HOLD[dom]); continue
            if ss.is_foreign(dom, vendor):
                drop('foreign'); continue
            if em in emails:
                drop('already on file'); continue
            b = ss.brand_key(dom)
            if b and b in bases:
                drop('business already in the campaign'); continue
            if em in seen:
                drop('duplicate in this batch'); continue
            name = ss.clean_vendor(vendor, dom)
            subject, body = render(name)
            if '{' in subject or '{' in body:
                drop('template placeholder left unfilled'); continue
            rows.append({'audience': 'vendor', 'email': em, 'business_name': name,
                         'to': em, 'subject': subject, 'body': body})
            seen.add(em)
            if b:
                bases.add(b)

    print(f'would add {len(rows)} vendors to the queue')
    for r in rows:
        print(f'  + {r["business_name"]:<28} {r["email"]}')
    if drops:
        print('\ndropped:')
        for why, who in sorted(drops.items()):
            print(f'  {why}: {len(who)}')
            for w in who[:8]:
                print(f'      - {w}')
    if not apply_it:
        print('\ndry run -- pass --apply to append')
        return
    if not rows:
        print('nothing to append')
        return
    with open(QUEUE, 'a', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, fieldnames=COLS).writerows(rows)
    print(f'\nappended {len(rows)} rows to {QUEUE}')


if __name__ == '__main__':
    main('--apply' in sys.argv)
