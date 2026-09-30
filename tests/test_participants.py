import unittest
from bs4 import BeautifulSoup
from app.participants import read_participants, write_participants
from app.myvendorlink import parse_participant_links, parse_vendor_profile, PREFIX
from app.project_fields import project_year, bidding_amount
from app.totalbiddata import balanced_records

class ParticipantTests(unittest.TestCase):
    def test_existing_total_bid_contacts_display_with_roles(self):
        row=dict(source_url='https://example.org',description='Plan Holders: Builder; phone: 123; email: one@builder.org, two@builder.org; amount: ')
        people=read_participants(row)
        self.assertEqual(people[0]['role'],'Plan Holders')
        self.assertIn('two@builder.org',people[0]['email'])
        self.assertNotIn('contractor',row)
    def test_participant_storage_is_idempotent(self):
        row={'description':'Scope: Roof'}
        write_participants(row,[dict(name='Builder',role='Planholder',email='')])
        write_participants(row,read_participants(row))
        self.assertEqual(row['description'].count('Project participants:'),1)
    def test_linked_profile_rejects_other_hosts_and_tokens(self):
        table=f'<table id="{PREFIX}grvPlanholders"><tr><td>Builder</td><td><a href="vendordetails?v=12">View</a></td></tr><tr><td>Other</td><td><a href="https://evil.org/vendordetails?v=1">View</a></td></tr></table>'
        people=parse_participant_links(BeautifulSoup(table,'html.parser'))
        self.assertEqual(len(people),1)
        self.assertEqual(people[0]['role'],'Planholder')
    def test_company_identity_and_primary_phone(self):
        fields={'lblTextCompanyName':'Prime Construction','rptVendorContacts_ctl00_lblTextContactLastName':'Allen','rptVendorContacts_ctl00_lblTextContactFirstName':'Mark','rptVendorContacts_ctl00_lblTextContactPhone':'(407) 856-8180','rptVendorContacts_ctl00_lblTextContactAddressTypeText':'Primary'}
        html=''.join(f'<span id="{PREFIX}{k}">{value}</span>' for k,value in fields.items())
        profile=parse_vendor_profile(html,'Prime Construction')
        self.assertEqual(profile['contact_name'],'Mark Allen')
        self.assertEqual(profile['phone'],'(407) 856-8180')
        self.assertEqual(profile['email'],'')
        with self.assertRaises(ValueError):
            parse_vendor_profile(html,'Other Company')
    def test_2026_occupancy_retains_sca_contractor(self):
        row=dict(source='sca_factsheet',description='Contract value: $66,677,000; Occupancy date: September 2026; Construction start: September 2023',contractor='Forte Construction')
        self.assertEqual(project_year(row),'2026')
        self.assertEqual(bidding_amount(row),'Contract value: $66,677,000')
        row['description']=row['description'].replace('September 2026','September 2020')
        self.assertEqual(project_year(row),'2020')
    def test_awarded_projects_not_starved_by_bidding(self):
        rows=[dict(source_id=str(i),stage='advertised',deadline='10/1/2026') for i in range(1,101)]
        rows.append(dict(source_id='101',stage='awarded',deadline='9/1/2026'))
        rows.append(dict(source_id='102',stage='awarded',deadline='9/1/2025'))
        ordered=balanced_records(rows,{})
        self.assertEqual(ordered[0]['source_id'],'101')
        self.assertFalse(any(r['source_id']=='102' for r in ordered))
