"""Read Total Bid Data contracts into the existing Projects ledger schema."""
import asyncio
import logging
import os
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from .project_fields import project_year

log = logging.getLogger(__name__)
BASE = 'https://admin.totalbiddata.com/'
QUEUES = ('BiddingContracts', 'FollowupContracts', 'BidResultsContracts', 'AwardedContracts')
STAGES = {'bidding': 'advertised', 'sub-bid': 'advertised', 'bid result': 'bid_result', 'award': 'awarded'}


def contract_url(contract_id):
    if not str(contract_id).isdigit():
        raise ValueError('Invalid Total Bid Data contract ID')
    return f'{BASE}index.cfm?cfaction=Contracts.View&ContractID={contract_id}'


def parse_listing(html):
    soup = BeautifulSoup(html, 'html.parser')
    rows = []
    for tr in soup.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) < 7:
            continue
        values = [c.get_text(' ', strip=True) for c in cells]
        if not values[0].isdigit() or not values[1]:
            continue
        rows.append(dict(source='totalbiddata', source_id=values[0],
                         source_url=contract_url(values[0]), title=values[1],
                         deadline=values[2], stage=STAGES.get(values[3].lower(), 'unknown'),
                         description=f'City: {values[4]}; State: {values[5]}; County: {values[6]}'))
    total = re.search(r'Results\s+[\d,]+\s*-\s*[\d,]+\s+of\s+([\d,]+)', soup.get_text(' ', strip=True), re.I)
    starts = []
    for a in soup.find_all('a', href=True):
        params = parse_qs(urlparse(a['href']).query)
        if params.get('startrow', [''])[0].isdigit():
            starts.append(int(params['startrow'][0]))
    return rows, int(total.group(1).replace(',', '')) if total else None, starts


def parse_contacts(html):
    soup = BeautifulSoup(html, 'html.parser')
    contacts = []
    for table in soup.select('table'):
        if table.find('table'):
            continue
        trs = table.find_all('tr')
        if len(trs) < 3:
            continue
        role = trs[0].get_text(' ', strip=True)
        headers = [c.get_text(' ', strip=True).lower() for c in trs[1].find_all(['th', 'td'], recursive=False)]
        if 'name' not in headers or 'email' not in headers:
            continue
        for tr in trs[2:]:
            values = [c.get_text(' ', strip=True) for c in tr.find_all('td', recursive=False)]
            fields = dict(zip(headers, values))
            if not fields.get('name'):
                continue
            email = fields.get('email', '').strip()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                email = ''
            contacts.append(dict(role=role, name=fields['name'], email=email,
                                 phone=fields.get('phone', ''), amount=fields.get('amount', '')))
    return contacts


def enrich_record(row, fields, contacts):
    result = dict(row)
    result['title'] = fields.get('contracttitle') or row['title']
    result['deadline'] = ' '.join(filter(None, [fields.get('BidDueDate'), fields.get('BidDueTime')])) or row['deadline']
    phase = fields.get('BidStageID', '').split('|')[-1].lower()
    result['stage'] = STAGES.get(phase, row['stage'])
    update = fields.get('ProjectUpdate', '')
    if re.search(r'\b(cancelled|canceled|closed without award)\b', update, re.I):
        result['stage'] = 'cancelled'
    details = [row.get('description', '')]
    for name in ('SolicitationNumber', 'ContractNumber', 'PinNumber', 'Location', 'ProjectUpdate', 'CompTime'):
        if fields.get(name):
            details.append(f'{name}: {fields[name]}')
    # A bidder is not necessarily the winner. Prefer explicitly awarded contacts.
    candidates = []
    for role in ('award', 'general contractor', 'contractor'):
        candidates = [c for c in contacts if c['role'].lower() == role]
        if candidates:
            break
    unique = {c['name'].casefold(): c for c in candidates}
    result['contractor'] = '; '.join(c['name'] for c in unique.values())
    if len(unique) == 1:
        contact = next(iter(unique.values()))
        result['contractor_email'] = contact['email']
        result['contractor_phone'] = contact['phone']
        if contact['email'] or contact['phone']:
            result['contact_evidence'] = row['source_url'] + '#ui-tabs-3'
    engineers = [c['name'] for c in contacts if c['role'].lower() in
                 ('architect', 'architect/engineer', 'engineer', 'm,p&e engineers', 'structural engineers')]
    result['architect_engineer'] = '; '.join(dict.fromkeys(engineers))
    source_contacts = [c for c in contacts if c['role'].lower() == 'contact'] or [c for c in contacts if c['role'].lower() == 'owner']
    if source_contacts:
        c = next((c for c in source_contacts if c['email']), source_contacts[0])
        result.update(source_contact_name=c['name'], source_contact_email=c['email'],
                      source_contact_phone=c['phone'], source_contact_role=c['role'],
                      source_contact_evidence=row['source_url'] + '#ui-tabs-3')
    for c in contacts:
        details.append(f"{c['role']}: {c['name']}; phone: {c['phone']}; email: {c['email']}; amount: {c['amount']}")
    amounts = [f"{c['role']} — {c['name']}: {c['amount']}" for c in contacts
               if c['role'].lower() in ('award', 'bidder', 'bid result', 'general contractor', 'contractor')
               and re.fullmatch(r'\$?\s*\d[\d,]*(?:\.\d+)?', c['amount'].strip())
               and float(c['amount'].strip().replace('$', '').replace(',', '')) > 0]
    if amounts:
        details.insert(0, 'Bidding amount: ' + '; '.join(amounts))
    result['description'] = '\n'.join(filter(None, details))
    return result


async def collect_totalbiddata():
    username, password = os.getenv('TOTALBIDDATA_USERNAME'), os.getenv('TOTALBIDDATA_PASSWORD')
    if not username or not password:
        return {'status': 'not_configured', 'message': 'Set TOTALBIDDATA_USERNAME and TOTALBIDDATA_PASSWORD in Render'}
    from playwright.async_api import async_playwright
    from .project_store import read_projects, upsert_projects
    max_pages = min(max(int(os.getenv('TOTALBIDDATA_MAX_PAGES', '1000')), 1), 1000)
    batch_size = min(max(int(os.getenv('TOTALBIDDATA_DETAIL_LIMIT', '100')), 1), 1000)
    existing = {r['source_id']: r for r in read_projects() if r['source'] == 'totalbiddata'}
    discovered, queue_summary = {}, {}
    stored = 0
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(BASE, wait_until='domcontentloaded', timeout=45000)
            await page.locator('input[name="username"]').fill(username)
            await page.locator('input[name="password"]').fill(password)
            await page.locator('input[name="frmsubmit"]').click()
            try:
                await page.locator('a[href*="passport.logout"]').wait_for(timeout=30000)
            except Exception:
                # Never include login HTML, session URLs, or credential values in logs.
                raise RuntimeError('Total Bid Data sign-in could not be verified') from None
            for queue in QUEUES:
                start, seen, total = 1, set(), None
                for _ in range(max_pages):
                    url = f'{BASE}index.cfm?cfaction=Contracts.{queue}&startrow={start}'
                    await page.goto(url, wait_until='domcontentloaded', timeout=45000)
                    if await page.locator('input[name="password"]').count():
                        raise RuntimeError('Total Bid Data session expired')
                    rows, total, starts = parse_listing(await page.content())
                    fresh = [r for r in rows if r['source_id'] not in seen]
                    if not fresh:
                        if total and len(seen) < total:
                            raise RuntimeError(f'Total Bid Data {queue} pagination stopped early')
                        break
                    for r in fresh:
                        seen.add(r['source_id'])
                        discovered[r['source_id']] = r
                    if total is not None and len(seen) >= total:
                        break
                    following = [n for n in starts if n > start]
                    if not following:
                        break
                    start = min(following)
                    await asyncio.sleep(0.5)
                queue_summary[queue] = {'discovered': len(seen), 'total': total,
                                         'complete': total is not None and len(seen) >= total}
            # Repeated runs backfill missing records, then refresh oldest records.
            ordered = balanced_records(discovered.values(), existing)
            pending = []
            failures = 0
            for row in ordered[:batch_size]:
                try:
                    await page.goto(row['source_url'], wait_until='domcontentloaded', timeout=45000)
                    if await page.locator('input[name="password"]').count():
                        raise RuntimeError('Total Bid Data session expired')
                    await page.locator('input[name="contracttitle"]').wait_for(timeout=20000)
                    fields = await page.locator('#ui-tabs-1').evaluate('''el => Object.fromEntries(
                        Array.from(el.querySelectorAll('input,textarea,select'))
                        .filter(e => e.name && e.type !== 'hidden' && e.type !== 'password')
                        .map(e => [e.name, e.value]))''')
                    await page.get_by_role('link', name='Contacts', exact=True).click()
                    await page.get_by_role('heading', name='Contract Contacts', exact=True).wait_for(timeout=20000)
                    html = await page.locator('#ui-tabs-3').inner_html()
                    pending.append(enrich_record(row, fields, parse_contacts(html)))
                    if len(pending) >= 20:
                        stored += upsert_projects(pending)
                        pending.clear()
                except Exception:
                    if await page.locator('input[name="password"]').count():
                        raise RuntimeError('Total Bid Data session expired') from None
                    failures += 1
                    log.warning('Total Bid Data detail failed for contract %s', row['source_id'])
                await asyncio.sleep(0.5)
            if pending:
                stored += upsert_projects(pending)
        finally:
            await browser.close()
    return {'status': 'partial' if failures or stored < len(ordered) or
             any(not q['complete'] for q in queue_summary.values()) else 'complete',
            'discovered': len(discovered), 'eligible_2026': len(ordered), 'stored': stored, 'detail_errors': failures,
            'remaining_unstored': sum(r['source_id'] not in existing for r in ordered[batch_size:]),
            'detail_limit': batch_size, 'queues': queue_summary}



def balanced_records(records, existing):
    """Refresh each project phase rather than letting bidding starve awarded GCs."""
    groups = {}
    for row in records:
        if project_year(row) != '2026':
            continue
        groups.setdefault(row.get('stage', 'unknown'), []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: (row['source_id'] in existing,
                                  existing.get(row['source_id'], {}).get('last_seen', ''),
                                  -int(row['source_id'])))
    ordered = []
    while any(groups.values()):
        for phase in ('awarded', 'bid_result', 'advertised', 'unknown'):
            if groups.get(phase):
                ordered.append(groups[phase].pop(0))
        for phase in sorted(set(groups) - {'awarded', 'bid_result', 'advertised', 'unknown'}):
            if groups[phase]:
                ordered.append(groups[phase].pop(0))
    return ordered
