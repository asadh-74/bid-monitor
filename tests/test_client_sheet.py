import unittest
from app.client_sheet import build_client_rows

class ClientSheetTests(unittest.TestCase):
    def project(self, **changes):
        return dict(source='totalbiddata', source_id='1', title='Roof', deadline='10/1/2026',
                    source_url='https://example.org/1', description='', **changes)

    def test_old_projects_and_duplicate_id_filtered(self):
        rows, contacts = build_client_rows([self.project(last_seen='2026-09-30'), self.project(last_seen='2026-10-01'),
                                            dict(source='x', source_id='2', title='Old', deadline='2025-01-01')])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][-1], '2026-10-01')

    def test_bidder_contact_does_not_become_general_contractor(self):
        record = self.project()
        record['description'] = 'Bidder: Builder; phone: 123; email: bids@example.org; amount: $100,000'
        rows, contacts = build_client_rows([record])
        self.assertEqual(rows[1][5], 'Not listed')
        self.assertIn('$100,000', rows[1][3])
        self.assertEqual(contacts[1][3:5], ['Bidder', 'Builder'])

    def test_procurement_contact_and_missing_email_stay_separate(self):
        rows, contacts = build_client_rows([self.project(contractor='Builder', source_contact_name='Buyer', source_contact_role='Procurement')])
        self.assertEqual(len(contacts), 3)
        self.assertEqual(contacts[1][6], 'Not listed')
        self.assertEqual(contacts[2][3], 'Procurement')
        self.assertEqual(contacts[2][5], 'Buyer')
