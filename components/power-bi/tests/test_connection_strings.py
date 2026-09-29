"""Connection strings: allowlist redaction, quote-aware parsing, and the live-connection wording."""
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from pbidocgen import connection_strings as cs, portable
from pbidocgen.agent_writer import build_agent_md
from pbidocgen.live_connection import describe
from pbidocgen.pbix_batch import load_extracted
from pbidocgen.renderer import build_payload
from pbidocgen.report_parser import parse_report

SAMPLES = Path(__file__).resolve().parent.parent / 'pbip-samples'
SECRETS = ('hunter2', 'SECRETTOKEN', 'k1k1k1', 'ZmFrZQ==', 'SIGSECRET', 'bob', 'pw@')


class Redaction(unittest.TestCase):
    def test_only_identity_keys_survive(self):
        raw = ('Data Source=srv;Initial Catalog=db;Password=hunter2;User ID=bob;Access Token=SECRETTOKEN;'
               'Application Key=k1k1k1;AccountKey=ZmFrZQ==;SharedAccessSignature=sv=1&sig=SIGSECRET;Whatever=x')
        self.assertEqual(cs.redact(raw), 'Data Source=srv;Initial Catalog=db')

    def test_a_quoted_secret_containing_semicolons_does_not_leak_a_fragment(self):
        out = cs.redact('Data Source=srv;Password="a;b=SIGSECRET";Initial Catalog=db')
        self.assertEqual(out, 'Data Source=srv;Initial Catalog=db')
        self.assertNotIn('SIGSECRET', out)

    def test_quoted_values_and_doubled_quotes(self):
        parsed = cs.values("Data Source=\"powerbi://x/y [DEV]\";Initial Catalog='it''s';Database={a;b}")
        self.assertEqual(parsed, {'data source': 'powerbi://x/y [DEV]', 'initial catalog': "it's", 'database': 'a;b'})

    def test_url_credentials_are_dropped(self):
        self.assertEqual(cs.redact('Data Source=https://bob:pw@host/x;Initial Catalog=c'),
                         'Data Source=https://host/x;Initial Catalog=c')

    def test_source_keys_also_keep_what_the_mashup_readers_need(self):
        raw = 'Provider=Microsoft.PowerBI.OLEDB.Mashup;Data Source=$Embedded$;Location="c:\\x;y";Mashup=QUJD;Password=hunter2'
        self.assertNotIn('Location', cs.redact(raw))
        kept = cs.redact(raw, cs.SOURCE_KEYS)
        self.assertIn('Location="c:\\x;y"', kept)
        self.assertIn('Mashup=QUJD', kept)
        self.assertNotIn('hunter2', kept)

    def test_describe_never_reports_credentials_in_server_or_connection_string(self):
        live = describe('Data Source=asazure://bob:pw@h/x;Initial Catalog=C;AccountKey=ZmFrZQ==;Access Token=SECRETTOKEN')
        self.assertEqual((live['server'], live['database']), ('asazure://h/x', 'C'))
        for secret in SECRETS:
            self.assertNotIn(secret, json.dumps(live))


class PortableDataSources(unittest.TestCase):
    """portable.model_document over a scripted metadata database (no PBIX needed)."""

    def database(self, connection, details=None):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        for ddl in ('CREATE TABLE Model (Name, DefaultMode, Culture)',
                    'CREATE TABLE "Table" (ID, Name, SystemFlags)',
                    'CREATE TABLE Column (ID, TableID, Type, SortByColumnID)',
                    'CREATE TABLE Partition (TableID, Type, Mode, QueryDefinition)',
                    'CREATE TABLE Measure (TableID, Name, Expression, FormatString, IsHidden)',
                    'CREATE TABLE DataSource (ID, Name, ConnectionString, ConnectionDetails)'):
            db.execute(ddl)
        db.execute("INSERT INTO Model VALUES ('M', 0, 'en-US')")
        db.execute("INSERT INTO DataSource VALUES (1, 'src', ?, ?)", (connection, details))
        return db

    def run_document(self, connection, details=None):
        with mock.patch.object(portable, '_metadata', return_value=self.database(connection, details)):
            return portable.model_document('m.abf')['model']['dataSources'][0]

    def test_connection_string_is_allowlisted(self):
        src = self.run_document('Data Source=srv;Initial Catalog=db;AccountKey=ZmFrZQ==;Access Token=SECRETTOKEN;'
                                'Password=hunter2')
        self.assertEqual(src['connectionString'], 'Data Source=srv;Initial Catalog=db')

    def test_connection_details_keep_the_address_and_drop_credentials(self):
        details = json.dumps({'protocol': 'tds', 'address': {'server': 's', 'database': 'd'},
                              'authentication': {'kind': 'UsernamePassword', 'password': 'hunter2'},
                              'credential': {'key': 'ZmFrZQ=='}})
        src = self.run_document('Data Source=srv', details)
        self.assertEqual(src['connectionDetails'], {'protocol': 'tds', 'address': {'server': 's', 'database': 'd'}})
        self.assertNotIn('hunter2', json.dumps(src))


class LiveConnectionWording(unittest.TestCase):
    """A live connection is not DirectQuery; the documents must not say it is."""

    def test_pbix_warning(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pbix = root / 'thin.pbix'
            with zipfile.ZipFile(pbix, 'w') as z:
                z.writestr('Connections', json.dumps({'Connections': [{'ConnectionString':
                           'Data Source=asazure://h/x;Initial Catalog=C'}]}))
            extract = root / 'x' / 'Report'
            extract.mkdir(parents=True)
            layout = {'id': 0, 'sections': [{'name': 'p', 'displayName': 'P', 'ordinal': 0, 'visualContainers': []}]}
            (extract / 'report.json').write_text(json.dumps(layout), encoding='utf-8')
            _, report = load_extracted(root / 'x', pbix, False)
        message = next(w['message'] for w in report['warnings'] if w['category'] == 'External semantic model')
        self.assertIn('live connection to a remote semantic model', message)
        self.assertNotIn('DirectQuery', message)

    def test_agent_context_and_html_note(self):
        report = parse_report(SAMPLES / 'thin-report-live-connection' / 'K201-MonthSlicer.Report')
        payload = build_payload(None, report, None, 'K201')
        agent = build_agent_md(payload)
        self.assertIn('live connection to', agent)
        self.assertNotIn('DirectQuery', agent)
        template = (Path(portable.__file__).parent / 'template.html').read_text(encoding='utf-8')
        self.assertIn('This report has a live connection to', template)
        self.assertNotIn('live-connected (DirectQuery)', template)


if __name__ == '__main__':
    unittest.main()
