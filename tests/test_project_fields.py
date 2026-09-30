import unittest
from app.project_fields import project_year, bidding_amount, amount_from_text
from app.totalbiddata import enrich_record

class ProjectFieldTests(unittest.TestCase):
    def test_scrape_time_does_not_make_old_project_new(self):
        self.assertEqual(project_year({'deadline':'1/2/2025','first_seen':'2026-09-30'}),'2025')
        self.assertEqual(project_year({'first_seen':'2026-09-30','source_id':'2026-1'}),'')
    def test_source_dates(self):
        for date in ('2026-09-30T00:00:00','09/30/2026 10 AM','September 2026'):
            self.assertEqual(project_year({'deadline':date}),'2026')
    def test_award_and_broadcast_years(self):
        self.assertEqual(project_year({'description':'Type: School; Contract award date: June 2015'}),'2015')
        self.assertEqual(project_year({'description':'Broadcast: 9/29/2026; Fiscal year: 2027'}),'2026')
    def test_currency_requires_financial_context(self):
        self.assertEqual(amount_from_text('Bid bond: $50,000'), '')
        self.assertEqual(amount_from_text('Phone: 2026; unrelated $123'), '')
        self.assertEqual(amount_from_text('Budget: $1,000,000 - $2,000,000'), 'Budget: $1,000,000 - $2,000,000')
    def test_sca_threshold_is_estimate(self):
        self.assertEqual(bidding_amount({'title':'PS021X Roofs Over 4M','source':'sca_limited'}),'Estimated range: over $4 million')
    def test_unknown_amount_is_blank(self):
        self.assertEqual(bidding_amount({'source':'myvendorlink','description':'Bond amount: $5,000'}),'')
    def test_bidder_amount_does_not_make_bidder_contractor(self):
        row=dict(source='totalbiddata',source_id='1',source_url='https://example.org',title='Roof',deadline='10/1/2026',stage='advertised')
        contacts=[dict(role='Bidder',name='Builder',email='',phone='',amount='$125,000')]
        record=enrich_record(row,{},contacts)
        self.assertEqual(record['contractor'],'')
        self.assertIn('Bidder — Builder: $125,000', bidding_amount(record))
