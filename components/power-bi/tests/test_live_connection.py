import json, tempfile, unittest, zipfile
from pathlib import Path
from pbidocgen.live_connection import describe, from_pbix, from_pbir, redact


class LiveConnectionTests(unittest.TestCase):
    def test_redacts_credentials_and_classifies_aas(self):
        live = describe('Data Source=asazure://uks.asazure.windows.net/srv;Initial Catalog=Sales;Password=x;User ID=u')
        self.assertEqual(live['kind'], 'Azure Analysis Services')
        self.assertEqual(live['database'], 'Sales')
        self.assertNotIn('Password', live['connectionString'])
        self.assertNotIn('User ID', redact('User ID=u;Data Source=a'))

    def test_pbix_connections_part(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'a.pbix'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('Connections', json.dumps({'Connections': [{'ConnectionString':
                    'Data Source=localhost;Initial Catalog=Cube'}]}))
            live = from_pbix(path)
        self.assertEqual((live['kind'], live['database']), ('SQL Server Analysis Services', 'Cube'))

    def test_pbir_by_connection(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'definition.pbir').write_text(json.dumps({'datasetReference': {'byConnection': {
                'connectionString': 'Data Source=asazure://a/b;Initial Catalog=C'}}}))
            self.assertEqual(from_pbir(Path(d))['server'], 'asazure://a/b')
            self.assertIsNone(from_pbir(Path(d) / 'missing'))


if __name__ == '__main__':
    unittest.main()


class QuotedConnectionStringTests(unittest.TestCase):
    def test_powerbi_xmla_with_quoted_values(self):
        # Shape taken from a public PBIR thin report (definition.pbir byConnection).
        live = describe('Data Source="powerbi://api.powerbi.com/v1.0/myorg/Report Templates [DEV]";'
                        'initial catalog="K201 - MonthSlicer";access mode=readonly;integrated security=ClaimsToken')
        self.assertEqual(live['kind'], 'Power BI semantic model (XMLA endpoint)')
        self.assertEqual(live['server'], 'powerbi://api.powerbi.com/v1.0/myorg/Report Templates [DEV]')
        self.assertEqual(live['database'], 'K201 - MonthSlicer')


class SourceRowAndPairingTests(unittest.TestCase):
    live = describe('Data Source=asazure://a/b;Initial Catalog=Sales')

    def test_source_row_and_summary_entry(self):
        from pbidocgen.live_connection import source_row
        from pbidocgen.renderer import build_payload
        self.assertEqual(source_row(self.live)['database'], 'Sales')
        report = dict(name='R', pages=[], manifest=[], warnings=[], liveConnection=self.live,
                      reportFilters=[], otherFields=[], bookmarks=[], customVisuals={})
        payload = build_payload(None, report, None, 'R')
        self.assertEqual(payload['liveSource']['sourceType'], 'Azure Analysis Services')
        self.assertIsNone(payload['livePairing'])

    def test_pairing_flags_name_mismatch(self):
        from pbidocgen.live_connection import pairing
        self.assertTrue(pairing(self.live, {'name': 'sales'})['nameMatches'])
        other = pairing(self.live, {'name': 'HR'})
        self.assertFalse(other['nameMatches'])
        self.assertIn('differs', other['note'])
        self.assertIsNone(pairing(None, {'name': 'x'}))
