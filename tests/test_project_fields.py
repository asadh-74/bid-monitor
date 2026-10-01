import unittest
from unittest.mock import AsyncMock, patch
import httpx
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
    def test_sca_budget_shorthand_and_month_date(self):
        self.assertEqual(amount_from_text('Budget: $4M OVER; Status: Design'), 'Budget: $4M OVER')
        self.assertEqual(amount_from_text('Budget: $500K - $750K'), 'Budget: $500K - $750K')
        self.assertEqual(project_year({'description':'Design completion date: 2026/11'}), '2026')
    def test_bid_result_amount_is_visible(self):
        self.assertIn('$123,000', bidding_amount({'description':'Bid Result: Builder; phone: ; email: ; amount: $123,000'}))
    def test_bidder_amount_does_not_make_bidder_contractor(self):
        row=dict(source='totalbiddata',source_id='1',source_url='https://example.org',title='Roof',deadline='10/1/2026',stage='advertised')
        contacts=[dict(role='Bidder',name='Builder',email='',phone='',amount='$125,000')]
        record=enrich_record(row,{},contacts)
        self.assertEqual(record['contractor'],'')
        self.assertIn('Bidder — Builder: $125,000', bidding_amount(record))

class SourceRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_503_is_retried(self):
        from app.public_sources import get_with_retry
        request = httpx.Request('GET', 'https://example.org/data')
        client = AsyncMock()
        client.get.side_effect = [httpx.Response(503, request=request), httpx.Response(200, request=request)]
        with patch('app.public_sources.asyncio.sleep', new_callable=AsyncMock):
            response = await get_with_retry(client, str(request.url))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get.await_count, 2)

    async def test_permanent_error_is_not_hidden(self):
        from app.public_sources import get_with_retry
        request = httpx.Request('GET', 'https://example.org/data')
        client = AsyncMock()
        client.get.return_value = httpx.Response(404, request=request)
        with self.assertRaises(httpx.HTTPStatusError):
            await get_with_retry(client, str(request.url))
        self.assertEqual(client.get.await_count, 1)


class AwardsOutageTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, responses):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from app.public_sources import collect_awards
        store = SimpleNamespace(upsert_projects=Mock(return_value=1))
        with patch.dict(sys.modules, {'app.project_store': store}), patch('app.public_sources.httpx.AsyncClient') as client, patch('app.public_sources.get_with_retry', new_callable=AsyncMock) as fetch:
            client.return_value.__aenter__ = AsyncMock()
            client.return_value.__aexit__ = AsyncMock(return_value=False)
            fetch.side_effect = responses
            result = await collect_awards()
        return result, store.upsert_projects

    def response(self, status, items=None):
        return httpx.Response(status, request=httpx.Request('GET', 'https://example.org/data'), json=items or [])

    async def test_one_outage_preserves_other_dataset(self):
        good = self.response(200, [{'upcoming_project_name':'School', 'upcoming_project_description':'Roof', 'upcoming_project_budget_range':'$4M OVER', 'upcoming_project_design_completion_date':'2026/11'}])
        bad = self.response(503)
        result, write = await self.collect([good, httpx.HTTPStatusError('Unavailable', request=bad.request, response=bad)])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['stored'], 1)
        self.assertEqual(len(write.call_args.args[0]), 1)
        self.assertEqual(len(result['warnings']), 1)

    async def test_both_outages_keep_saved_data(self):
        result, write = await self.collect([httpx.ConnectError('Unavailable'), httpx.ConnectError('Unavailable')])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['stored'], 0)
        write.assert_not_called()
        self.assertEqual(len(result['warnings']), 2)

    async def test_permanent_source_error_still_fails(self):
        bad = self.response(404)
        with self.assertRaises(httpx.HTTPStatusError):
            await self.collect([httpx.HTTPStatusError('Missing source', request=bad.request, response=bad)])
