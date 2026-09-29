import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pbidocgen.catalog import build_catalog, describe_html, json_script, read_metadata, validate_metadata
from pbidocgen.renderer import build_payload, render_html


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def payload(self):
        return build_payload(None, None, None, 'Test report')

    def test_metadata_preserved_on_regeneration_and_explicitly_overridden(self):
        path = self.root / 'report.html'
        metadata = {'reportLocation': r'C:\Reports\Finance\Sales.pbip', 'connections': [
            {'server': 'sql01', 'database': 'DW', 'username': r'CORP\report_reader'}]}
        render_html(dict(self.payload(), documentation=metadata), path)
        render_html(self.payload(), path)
        self.assertEqual(read_metadata(path.read_text()), validate_metadata(metadata))
        render_html(dict(self.payload(), documentation={}), path)
        self.assertEqual(read_metadata(path.read_text()), validate_metadata({}))

    def test_metadata_is_escaped_and_never_executed(self):
        metadata = {'folder': '</script><script>throw Error(1)</script>'}
        path = render_html(dict(self.payload(), documentation=metadata), self.root / 'x.html')
        self.assertEqual(read_metadata(path.read_text())['folder'], metadata['folder'])
        self.assertNotIn('</script>', json_script(metadata))
        row = describe_html(path.read_text(), path.name)
        self.assertEqual(row['metadata']['folder'], metadata['folder'])

    def test_rejects_secret_fields_and_malformed_shapes(self):
        for value in ({'password': 'x'}, {'connections': [{'token': 'x'}]}, {'connections': 'x'},
                      {'folder': []}, {'connections': [{'username': 42}]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_metadata(value)

    def test_catalog_existing_legacy_plain_new_and_malformed(self):
        render_html(dict(self.payload(), documentation={'folder': 'Finance/Monthly'}), self.root / 'Sales #1.html')
        (self.root / 'legacy.html').write_text('<script>const DATA = {"title":"Legacy", "generated":"Yesterday"};\n</script>')
        (self.root / 'plain.htm').write_text('<html>Plain report</html>')
        (self.root / 'bad.html').write_text('<script id="pbi-documentation-metadata">not json</script>')
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / 'excluded.html').write_text('excluded')
        home = build_catalog(self.root)
        for _ in range(2):
            text = build_catalog(self.root).read_text()
            rows, _ = json.JSONDecoder().raw_decode(text.split('let entries=')[1])
            self.assertEqual(len(rows), 4)
            self.assertEqual(next(r for r in rows if r['title']=='Legacy')['generated'], 'Yesterday')
            self.assertEqual(next(r for r in rows if r['title']=='Test report')['href'], 'Sales%20%231.html')
            self.assertIn('warning', next(r for r in rows if r['filename']=='bad.html'))
        self.assertIsNone(describe_html(home.read_text(), home.name))

    def test_refuses_to_overwrite_non_catalogue_and_invalid_existing_metadata(self):
        home = self.root / 'pbi-home.html'
        home.write_text('my own home page')
        with self.assertRaises(ValueError):
            build_catalog(self.root)
        self.assertEqual(home.read_text(), 'my own home page')
        path = self.root / 'bad.html'
        path.write_text('<script id="pbi-documentation-metadata">bad</script>')
        with self.assertRaises(ValueError):
            render_html(self.payload(), path)
        self.assertIn('>bad<', path.read_text())

    def test_catalog_only_cli(self):
        (self.root / 'plain.html').write_text('hello')
        result = subprocess.run([sys.executable, 'generate_docs.py', '--catalog', str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'pbi-home.html').exists())

    def test_catalog_javascript_grouping_search_and_links(self):
        import shutil
        if not shutil.which('node'):
            self.skipTest('Node needed for catalogue interaction checks')
        home = build_catalog(self.root)
        result = subprocess.run(['node', str(Path(__file__).with_name('check_catalog.cjs')), str(home)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_report_summary_feeds_the_hub_index(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'samples'))
        from build_retail_sample import build_retail_payload
        from pbidocgen.catalog import clean_summary
        payload = build_retail_payload()
        summary = payload['summary']
        self.assertEqual({s['label'] for s in summary['sources']},
                         {'SQL Server \u00b7 finance-sql.corp.local / FinanceDW',
                          'SharePoint file \u00b7 Budget FY26.xlsx', 'CSV file \u00b7 targets.csv'})
        self.assertEqual(summary['counts'], {'tables': 7, 'columns': 45, 'measures': 15, 'pages': 3, 'visuals': 10})
        self.assertEqual((summary['coverageIssues'], summary['deletionCandidates'], summary['needsReview']), (1, 15, 2))
        self.assertEqual((summary['measuresDescribed'], summary['duplicateMeasureSets']), (2, 1))
        with tempfile.TemporaryDirectory() as d:
            render_html(payload, Path(d) / 'Retail.html')
            row = describe_html((Path(d) / 'Retail.html').read_text(encoding='utf-8'), 'Retail.html')
        self.assertEqual(row['mode'], 'combined')
        self.assertEqual(row['summary'], clean_summary(summary))
        # Untrusted summaries keep only known plain fields.
        self.assertEqual(clean_summary({'counts': {'tables': -1, 'pages': '3'}, 'sources': [{'label': 1, 'tables': ['A', 2]}],
                                        'evil': '<script>'}),
                         {'counts': {'tables': 0, 'columns': 0, 'measures': 0, 'pages': 0, 'visuals': 0},
                          'sources': [{'label': '', 'sourceType': '', 'server': '', 'database': '', 'location': '', 'tables': ['A']}],
                          'coverageIssues': 0, 'deletionCandidates': 0, 'needsReview': 0,
                          'measuresDescribed': 0, 'duplicateMeasureSets': 0})
