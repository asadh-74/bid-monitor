"""Authenticated VendorLink listings, using the unchanged Projects schema."""
import os
import re
from urllib.parse import unquote
from bs4 import BeautifulSoup

ACTIVE = 'https://www.myvendorlink.com/internal/vendor/active'
PREFIX = 'ctl00_RegionMiddle_'
GRID = PREFIX + 'grvSolicitations'


def parse_listing(html):
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find(id=GRID)
    if table is None:
        raise ValueError('VendorLink active-bid table missing')
    rows, pages = [], {1}
    for a in table.select('a[href]'):
        match = re.search(r'Page\$(\d+)', a['href'])
        if match:
            pages.add(int(match.group(1)))
    for tr in table.find_all('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) != 10:
            continue
        link = cells[9].find('a', title='View')
        if not link or not re.fullmatch(GRID + r'_ctl\d+_btnView', link.get('id', '')):
            continue
        values = [c.get_text(' ', strip=True) for c in cells]
        agency, number, title = values[:3]
        if not agency or not number or not title:
            continue
        rows.append(dict(source='myvendorlink', source_id=agency + '|' + number,
                         source_url=ACTIVE, title=title, stage='advertised',
                         deadline=values[8], description=f'Agency: {agency}; Solicitation: {number}',
                         _agency=agency, _number=number, _view_id=link['id']))
    total = re.search(r'Total:\s*([\d,]+)', soup.get_text(' ', strip=True))
    return rows, sorted(pages), int(total.group(1).replace(',', '')) if total else None


def parse_detail(html, row):
    soup = BeautifulSoup(html, 'html.parser')
    def field(name):
        el = soup.find(id=PREFIX + name)
        return el.get_text(' ', strip=True) if el else ''
    if field('lblAgency') != row['_agency'] or field('lblNumber') != row['_number']:
        raise ValueError('VendorLink detail does not match listing')
    result = {k: v for k, v in row.items() if not k.startswith('_')}
    result['title'] = field('lblTitle') or row['title']
    result['deadline'] = ' '.join(filter(None, [field('lblDueDate'), field('lblTimeZone')])) or row['deadline']
    status = field('lblStatus')
    result['stage'] = {'active': 'advertised', 'awarded': 'awarded', 'cancelled': 'cancelled',
                       'canceled': 'cancelled', 'closed': 'closed'}.get(status.lower(), 'unknown')
    contact = soup.find(id=PREFIX + 'hlContact')
    email = ''
    if contact and contact.get('href', '').lower().startswith('mailto:'):
        candidate = unquote(contact['href'][7:].split('?')[0]).strip()
        if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', candidate):
            email = candidate
    result.update(source_contact_name=field('hlContact'), source_contact_email=email,
                  source_contact_role='Primary procurement contact', source_contact_evidence=ACTIVE)
    result['description'] = '; '.join([row['description']] + [f'{label}: {field(name)}' for label, name in
        [('Status', 'lblStatus'), ('Fiscal year', 'lblFiscalYear'), ('Type', 'lblType'),
         ('Scope', 'lblNotes'), ('Questions due', 'lblQuestionEndDate'), ('Broadcast', 'lblBroadcastDate')]
        if field(name)])
    # Planholders and bidders are not evidence of an awarded contractor.
    return result


async def collect_myvendorlink():
    email, password = os.getenv('MYVENDORLINK_EMAIL'), os.getenv('MYVENDORLINK_PASSWORD')
    if not email or not password:
        return {'status': 'not_configured', 'discovered': 0, 'stored': 0}
    from playwright.async_api import async_playwright
    from .project_store import read_projects, upsert_projects
    limit = min(max(int(os.getenv('MYVENDORLINK_DETAIL_LIMIT', '100')), 1), 1000)
    max_pages = min(max(int(os.getenv('MYVENDORLINK_MAX_PAGES', '100')), 1), 1000)
    existing = {r['source_id']: r for r in read_projects() if r['source'] == 'myvendorlink'}
    discovered, total, complete = {}, None, False
    stored, failures, pending = 0, 0, []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(ACTIVE, wait_until='domcontentloaded', timeout=45000)
            if await page.locator('#' + PREFIX + 'txtPassword').count():
                await page.locator('#' + PREFIX + 'txtUsername').fill(email)
                await page.locator('#' + PREFIX + 'txtPassword').fill(password)
                await page.locator('#' + PREFIX + 'btnSubmit').click()
            try:
                await page.locator('#' + GRID).wait_for(timeout=30000)
            except Exception:
                raise RuntimeError('VendorLink sign-in could not be verified') from None
            async def listing_page(number):
                await page.goto(ACTIVE, wait_until='domcontentloaded', timeout=45000)
                await page.locator('#' + GRID).wait_for(timeout=20000)
                # Exact observed ASP.NET postback destination avoids matching 2 against 12.
                pager = page.locator('#' + GRID + f' a[href="javascript:__doPostBack(\'ctl00$RegionMiddle$grvSolicitations\',\'Page${number}\')"]')
                if await pager.count():
                    async with page.expect_navigation(wait_until="domcontentloaded"):
                        await pager.click()
                await page.locator('#' + GRID).wait_for(timeout=20000)
                return parse_listing(await page.content())
            for number in range(1, max_pages + 1):
                rows, pages, total = await listing_page(number)
                fresh = 0
                for row in rows:
                    if row['source_id'] not in discovered:
                        row['_page'] = number
                        discovered[row['source_id']] = row
                        fresh += 1
                if total is not None and len(discovered) >= total:
                    complete = True
                    break
                if not fresh or number + 1 not in pages:
                    break
            ordered = sorted(discovered.values(), key=lambda r: (
                r['source_id'] in existing, existing.get(r['source_id'], {}).get('last_seen', '')))
            for row in ordered[:limit]:
                try:
                    rows, _, _ = await listing_page(row['_page'])
                    current = next((r for r in rows if r['source_id'] == row['source_id']), None)
                    if current is None:
                        raise ValueError('Listing changed during collection')
                    async with page.expect_navigation(wait_until='domcontentloaded'):
                        await page.locator('#' + current['_view_id']).click()
                    await page.locator('#' + PREFIX + 'lblTitle').wait_for(timeout=20000)
                    pending.append(parse_detail(await page.content(), current))
                    if len(pending) >= 20:
                        stored += upsert_projects(pending)
                        pending.clear()
                except Exception:
                    failures += 1
                    # No credentials, session URLs, or authenticated HTML in logs.
            if pending:
                stored += upsert_projects(pending)
        finally:
            await browser.close()
    return {'status': 'complete' if complete and not failures and len(ordered) <= limit else 'partial',
            'discovered': len(discovered), 'total': total, 'stored': stored,
            'detail_errors': failures, 'detail_limit': limit, 'listing_complete': complete}
