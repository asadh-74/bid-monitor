import unittest
from app.totalbiddata import parse_listing, parse_contacts, enrich_record, contract_url


def table(role, entries):
    headers = ['', 'Name', 'City', 'State', 'Zip', 'Phone', 'Fax', 'Email', 'Amount', '']
    rows = ''.join('<tr>' + ''.join(f'<td>{v}</td>' for v in entry) + '</tr>' for entry in entries)
    return '<table><tr><th colspan="10">' + role + '</th></tr><tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr>' + rows + '</table>'


class TotalBidDataTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(source='totalbiddata', source_id='30177', source_url=contract_url('30177'),
                        title='ADA RAMP IMPROVEMENTS AT TOWN HALL', stage='awarded', deadline='07/14/2026')
        self.award = ['1', 'Laser Industries Inc.', 'Ridge', 'NY', '11961', '(631) 924-0644', '', 'NickC@laserindustriesinc.com', '$77,900.00', '']
        self.bidder = ['2', 'Other bidder', '', '', '', '', '', 'bidder@example.com', '', '']
        self.owner = ['1', 'Town of Brookhaven', '', '', '', '631-451-6249', '', 'owner@example.com', '', '']

    def test_awarded_contact_is_not_confused_with_bidder_or_owner(self):
        html = table('Award', [self.award]) + table('Bid Result', [self.bidder]) + table('Owner', [self.owner])
        r = enrich_record(self.row, {'ContractNumber': 'PV279ACON', 'BidStageID': '4|Award'}, parse_contacts(html))
        self.assertEqual(r['contractor'], 'Laser Industries Inc.')
        self.assertEqual(r['contractor_email'], 'NickC@laserindustriesinc.com')
        self.assertEqual(r['contractor_phone'], '(631) 924-0644')
        self.assertEqual(r['source_contact_email'], 'owner@example.com')
        self.assertIn('PV279ACON', r['description'])
        self.assertNotIn('CFID', r['contact_evidence'])

    def test_bid_results_do_not_invent_awarded_contractor(self):
        r = enrich_record(self.row, {}, parse_contacts(table('Bid Result', [self.bidder])))
        self.assertEqual(r['contractor'], '')
        self.assertNotIn('contractor_email', r)

    def test_multiple_awards_do_not_merge_recipients(self):
        r = enrich_record(self.row, {}, parse_contacts(table('Award', [self.award, self.bidder])))
        self.assertIn('Laser Industries Inc.', r['contractor'])
        self.assertNotIn('contractor_email', r)

    def test_cancellation_overrides_award_queue(self):
        r = enrich_record(self.row, {'ProjectUpdate': 'Project has been cancelled', 'BidStageID': '4|Award'}, [])
        self.assertEqual(r['stage'], 'cancelled')

    def test_listing_pagination_ignores_headers_and_session_parameters(self):
        html = '<table><tr><td>30177</td><td>ADA ramp</td><td>07/14/2026</td><td>Award</td><td>Farmingville</td><td>NY</td><td>Suffolk</td></tr></table>Results 1 - 20 of 966 <a href="index.cfm?cfaction=Contracts.AwardedContracts&amp;startrow=21&amp;CFID=secret">Next</a>'
        rows, total, starts = parse_listing(html)
        self.assertEqual(total, 966)
        self.assertEqual(starts, [21])
        self.assertEqual(rows[0]['source_id'], '30177')
        self.assertNotIn('CFID', rows[0]['source_url'])

    def test_bad_email_stays_blank(self):
        bad = self.award.copy(); bad[7] = 'not an email'
        self.assertEqual(parse_contacts(table('Award', [bad]))[0]['email'], '')


if __name__ == '__main__':
    unittest.main()
