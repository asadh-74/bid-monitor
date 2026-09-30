"""Authenticated VendorLink listings, using the unchanged Projects schema."""
import os
import re
from urllib.parse import unquote, urljoin, urlparse
from datetime import datetime, timezone, timedelta
from .participants import read_participants, write_participants
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
    due = field('lblDueDate') or row['deadline']
    zone = field('lblTimeZone')
    result['deadline'] = (due if not zone or due.endswith(' ' + zone) else due + ' ' + zone) if due else 'Not listed'
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
    return write_participants(result, parse_participant_links(soup))


async def collect_myvendorlink():
    email, password = os.getenv('MYVENDORLINK_EMAIL'), os.getenv('MYVENDORLINK_PASSWORD')
    if not email or not password:
        return {'status': 'not_configured', 'discovered': 0, 'stored': 0}
    from playwright.async_api import async_playwright
    from .project_store import read_projects, upsert_projects
    limit = min(max(int(os.getenv('MYVENDORLINK_DETAIL_LIMIT', '100')), 1), 1000)
    max_pages = min(max(int(os.getenv('MYVENDORLINK_MAX_PAGES', '100')), 1), 1000)
    existing = {r['source_id']: r for r in read_projects() if r['source'] == 'myvendorlink'}
    profile_limit = min(max(int(os.getenv('MYVENDORLINK_PROFILE_LIMIT', '100')), 1), 1000)
    profile_count, profile_errors = 0, 0
    profile_cache = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    for saved in existing.values():
        for participant in read_participants(saved):
            if participant.get('profile_checked', '') >= cutoff:
                profile_cache[participant.get('evidence', '')] = participant
    discovered, total, complete = {}, None, False
    stored, failures, pending = 0, 0, []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            profile_page = await context.new_page()
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
                    detail = parse_detail(await page.content(), current)
                    participants = read_participants(detail)
                    for participant in participants:
                        url = participant['evidence']
                        cached = profile_cache.get(url)
                        if cached and cached['name'].casefold() == participant['name'].casefold():
                            participant.update({k: v for k, v in cached.items() if k != 'role'})
                        elif profile_count < profile_limit:
                            profile_count += 1
                            try:
                                await profile_page.goto(url, wait_until='domcontentloaded', timeout=30000)
                                await profile_page.locator('#' + PREFIX + 'lblTextCompanyName').wait_for(timeout=15000)
                                participant.update(parse_vendor_profile(await profile_page.content(), participant['name']))
                                participant['profile_checked'] = datetime.now(timezone.utc).isoformat()
                                profile_cache[url] = dict(participant)
                            except Exception:
                                profile_errors += 1
                    pending.append(write_participants(detail, participants))
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
    return {'status': 'complete' if complete and not failures and not profile_errors and len(ordered) <= limit else 'partial',
            'discovered': len(discovered), 'total': total, 'stored': stored,
            'detail_errors': failures, 'detail_limit': limit, 'listing_complete': complete,
            'profiles_checked': profile_count, 'profile_errors': profile_errors, 'profile_limit': profile_limit}



def parse_participant_links(soup):
    result = []
    for selector, role in (('#' + PREFIX + 'grvPlanholders', 'Planholder'),
                           ('#' + PREFIX + 'divBidder', 'Bidder'),
                           ('#' + PREFIX + 'divSupplemental', 'Supplemental vendor')):
        for link in soup.select(selector + ' a[href]'):
            url = urljoin(ACTIVE, link['href'])
            parts = urlparse(url)
            if parts.hostname != 'www.myvendorlink.com' or parts.path != '/internal/vendor/vendordetails' or not re.fullmatch(r'v=\d+', parts.query):
                continue
            tr = link.find_parent('tr')
            cells = tr.find_all('td', recursive=False) if tr else []
            name = cells[0].get_text(' ', strip=True) if cells else link.get_text(' ', strip=True)
            if name:
                result.append(dict(name=name, role=role, evidence=url, email='', phone=''))
    return result


def parse_vendor_profile(html, expected_name):
    soup = BeautifulSoup(html, 'html.parser')
    def field(name):
        element = soup.find(id=PREFIX + name)
        return element.get_text(' ', strip=True) if element else ''
    actual = field('lblTextCompanyName')
    if ' '.join(actual.casefold().split()) != ' '.join(expected_name.casefold().split()):
        raise ValueError('Vendor profile does not match linked company')
    contacts = []
    for node in soup.select('span[id$="_lblTextContactLastName"]'):
        base = node['id'].rsplit('_lblTextContactLastName', 1)[0]
        def contact_field(suffix):
            value = soup.find(id=base + '_' + suffix)
            return value.get_text(' ', strip=True) if value else ''
        name = ' '.join(filter(None, [contact_field('lblTextContactFirstName'), node.get_text(' ', strip=True)]))
        emails = []
        for email_node in soup.select('[id^="' + base + '_"][id*="Email"]'):
            emails.extend(re.findall(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+', email_node.get_text(' ', strip=True)))
        contacts.append(dict(name=name, phone=contact_field('lblTextContactPhone'),
                             email='; '.join(dict.fromkeys(emails)),
                             role=contact_field('lblTextContactAddressTypeText')))
    primary = next((c for c in contacts if c['role'].lower() == 'primary'), contacts[0] if contacts else {})
    return dict(name=actual, phone=primary.get('phone', ''), email=primary.get('email', ''),
                contact_name=primary.get('name', ''), contacts=contacts)
