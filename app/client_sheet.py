"""Organized client views derived from the unchanged Projects ledger."""
from .project_fields import display_project

PROJECT_HEADERS = ['Source', 'Project ID', 'Project Name', 'Bidding Amount / Published Value', 'Deadline',
                   'General Contractor', 'Architect / Engineer', 'Contractor Email', 'Contractor Phone',
                   'Agency Contact', 'Agency Email', 'Agency Phone', 'Participating Companies',
                   'Source URL', 'Contact Evidence', 'First Seen UTC', 'Last Seen UTC']
CONTACT_HEADERS = ['Source', 'Project ID', 'Project Name', 'Role', 'Company / Organization',
                   'Contact Name', 'Email', 'Phone', 'Evidence URL', 'Last Seen UTC']
TABS = ('Client Projects', 'Project Contacts')


def build_client_rows(records):
    """Keep source identities distinct; only include projects with 2026 source dates."""
    unique = {}
    for record in records:
        row = display_project(record)
        if row['project_year'] != '2026' or not row.get('source_id') or not row.get('title'):
            continue
        key = (row['source'], row['source_id'])
        if key not in unique or row.get('last_seen', '') >= unique[key].get('last_seen', ''):
            unique[key] = row
    projects, contacts = [PROJECT_HEADERS], [CONTACT_HEADERS]
    for row in sorted(unique.values(), key=lambda r: (r['source'], r['title'].casefold(), r['source_id'])):
        participants = row['participants']
        companies = '\n'.join(dict.fromkeys(f"{p['name']} ({p['role']})" for p in participants))
        values = [row['source'], row['source_id'], row['title'], row['bidding_amount'], row.get('deadline'),
                  row.get('contractor'), row.get('architect_engineer'), row.get('contractor_email'),
                  row.get('contractor_phone'), row.get('source_contact_name'), row.get('source_contact_email'),
                  row.get('source_contact_phone'), companies, row.get('source_url'), row.get('contact_evidence'),
                  row.get('first_seen'), row.get('last_seen')]
        projects.append([str(v) if v else 'Not listed' for v in values])
        entries = []
        if row.get('contractor'):
            entries.append(dict(role='General contractor / awarded contractor', name=row['contractor'],
                                email=row.get('contractor_email', ''), phone=row.get('contractor_phone', ''),
                                evidence=row.get('contact_evidence') or row.get('source_url', '')))
        if row.get('architect_engineer'):
            entries.append(dict(role='Architect / engineer', name=row['architect_engineer'], evidence=row['source_url']))
        if row.get('source_contact_name'):
            entries.append(dict(role=row.get('source_contact_role') or 'Agency procurement contact', name='',
                                contact_name=row['source_contact_name'], email=row.get('source_contact_email', ''),
                                phone=row.get('source_contact_phone', ''), evidence=row.get('source_contact_evidence') or row['source_url']))
        for participant in participants:
            detailed = participant.get('contacts') or []
            if detailed:
                for contact in detailed:
                    entries.append({**participant, 'contact_name': contact.get('name', ''),
                                    'email': contact.get('email', ''), 'phone': contact.get('phone', '')})
            else:
                entries.append(participant)
        seen = set()
        for entry in entries:
            detail = [entry.get(k, '') for k in ('role', 'name', 'contact_name', 'email', 'phone', 'evidence')]
            identity = tuple(str(v).strip().casefold() for v in detail)
            if identity in seen:
                continue
            seen.add(identity)
            contacts.append([row['source'], row['source_id'], row['title']] +
                            [str(v) if v else 'Not listed' for v in detail] + [row.get('last_seen', '')])
    return projects, contacts


def refresh_client_views():
    """Rebuild only the two generated client tabs after each collection."""
    from .project_store import _service, read_projects
    service, spreadsheet_id = _service()
    projects, contacts = build_client_rows(read_projects(private=True))
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields='sheets.properties').execute()
    sheets = {s['properties']['title']: s['properties'] for s in meta.get('sheets', [])}
    requests = []
    for title, values in zip(TABS, (projects, contacts)):
        props = sheets.get(title)
        if props is None:
            requests.append({'addSheet': {'properties': {'title': title, 'gridProperties': {'rowCount': max(2000, len(values) + 100), 'columnCount': len(values[0]), 'frozenRowCount': 1}}}})
        elif len(values) > props['gridProperties']['rowCount']:
            requests.append({'updateSheetProperties': {'properties': {'sheetId': props['sheetId'], 'gridProperties': {'rowCount': len(values) + 100}}, 'fields': 'gridProperties.rowCount'}})
    if requests:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': requests}).execute()
    service.spreadsheets().values().batchClear(spreadsheetId=spreadsheet_id,
        body={'ranges': ["'Client Projects'!A:Q", "'Project Contacts'!A:J"]}).execute()
    service.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id,
        body={'valueInputOption': 'RAW', 'data': [{'range': "'Client Projects'!A1", 'values': projects},
                                               {'range': "'Project Contacts'!A1", 'values': contacts}]}).execute()
    return {'projects': len(projects) - 1, 'contacts': len(contacts) - 1}
