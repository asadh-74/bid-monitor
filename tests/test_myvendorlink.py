import unittest
from app.myvendorlink import parse_listing, parse_detail, ACTIVE, PREFIX, GRID

class VendorLinkTests(unittest.TestCase):
    def listing(self, agency='Airport'):
        cells = [agency,'2026-1','Roof repair','','Active','9/29/2026','','10/1/2026','11/5/2026',
                 f'<a id="{GRID}_ctl02_btnView" title="View">View</a>']
        return f'<table id="{GRID}"><tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr></table><span>Total: 1</span>'

    def detail(self, **overrides):
        fields = dict(lblAgency='Airport',lblNumber='2026-1',lblTitle='Roof repair',lblDueDate='11/5/2026 3:00 PM',lblTimeZone='EST',lblStatus='Active')
        fields.update(overrides)
        return ''.join(f'<span id="{PREFIX}{k}">{v}</span>' for k,v in fields.items()) + f'<a id="{PREFIX}hlContact" href="mailto:buyer@example.org">Jane Buyer</a><table><tr><td>Planholders</td><td>Roofing LLC</td></tr></table>'

    def test_identity_includes_agency(self):
        first, _, total = parse_listing(self.listing())
        other, _, _ = parse_listing(self.listing('City'))
        self.assertNotEqual(first[0]['source_id'], other[0]['source_id'])
        self.assertEqual(total,1)
        self.assertEqual(first[0]['source_url'],ACTIVE)

    def test_procurement_contact_is_not_contractor(self):
        rows,_,_ = parse_listing(self.listing())
        result = parse_detail(self.detail(), rows[0])
        self.assertEqual(result['source_contact_name'],'Jane Buyer')
        self.assertEqual(result['source_contact_email'],'buyer@example.org')
        self.assertNotIn('contractor',result)
        self.assertFalse(any(k.startswith('_') for k in result))
        self.assertEqual(result['deadline'],'11/5/2026 3:00 PM EST')

    def test_wrong_session_detail_rejected(self):
        rows,_,_ = parse_listing(self.listing())
        with self.assertRaises(ValueError):
            parse_detail(self.detail(lblNumber='OTHER'),rows[0])

    def test_login_html_rejected(self):
        with self.assertRaises(ValueError):
            parse_listing('<input type="password">')

    def test_exact_pagination(self):
        html=self.listing().replace('</table>', '<tr><td><a href="javascript:__doPostBack(\'grid\',\'Page$2\')">2</a></td></tr></table>')
        self.assertEqual(parse_listing(html)[1],[1,2])
