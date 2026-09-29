import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_column_usage import raw_model, report_fixture
from pbidocgen.model_parser import parse_model
from pbidocgen.linker import link
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.source_queries import build_source_queries


class SourceQueryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def payload(self):
        raw = raw_model()
        raw['model']['expressions'] = [
            {'name': 'Staging, "Café"', 'kind': 'm', 'expression': ['let', '    Text = "a,b""c"', 'in Text']},
            {'name': 'Parameter', 'kind': 'm', 'expression': '-1'},
            {'name': 'OtherLanguage', 'kind': 'dax', 'expression': '1'}]
        raw['model']['tables'][1]['partitions'] = [
            {'name': 'Live', 'source': {'type': 'm', 'expression': 'let X = 1 in X'}},
            {'name': 'Archive', 'source': {'type': 'm', 'expression': 'let X = 2 in X'}},
            {'name': 'SQL', 'source': {'type': 'query', 'query': 'select 1'}},
            {'name': 'Calculated', 'source': {'type': 'calculated', 'expression': '{1}'}},
            {'name': 'Lake', 'source': {'type': 'entity', 'entityName': 'T'}}]
        path = self.root / 'model.bim'
        path.write_text(json.dumps(raw))
        model = parse_model(path)
        report = report_fixture()
        report['name'] = 'Sales / Q4: Café?'
        return build_payload(model, report, link(model, report), 'Source export')

    def test_query_inventory_includes_m_and_shared_expressions_only(self):
        p = self.payload()
        rows = p['sourceQueries']
        self.assertEqual({r['queryName'] for r in rows}, {'Sales', 'Dim / Live', 'Dim / Archive', 'Staging, "Café"', 'Parameter'})
        self.assertTrue(all(r['report'] == 'Sales / Q4: Café?' for r in rows))
        self.assertIn('\n', next(r for r in rows if r['queryName'].startswith('Staging'))['mCode'])
        self.assertEqual(build_source_queries(None, p['report']), [])
        self.assertEqual(build_source_queries(p['model'], None)[0]['report'], 'Sales')

    def test_tmdl_shared_expressions_are_retained(self):
        definition = self.root / 'Shared.SemanticModel' / 'definition'
        definition.mkdir(parents=True)
        (definition / 'model.tmdl').write_text('model Model\n')
        (definition / 'expressions.tmdl').write_text('expression Stage =\n\t\tlet X = 1 in X\n\tkind: m\n')
        model = parse_model(definition.parent)
        self.assertEqual(model['expressions'][0]['name'], 'Stage')
        self.assertIn('let X = 1 in X', build_source_queries(model, None)[0]['mCode'])

    @unittest.skipUnless(shutil.which('node'), 'Node needed for CSV download checks')
    def test_actual_csv_downloads_and_round_trip(self):
        p = self.payload()
        html = render_html(p, self.root / 'out.html')
        output = self.root / 'download.csv'
        result = subprocess.run(['node', str(Path(__file__).with_name('check_source_queries.cjs')), str(html), str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with output.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(reader.fieldnames, ['report', 'query name', 'query m code'])
        self.assertEqual(len(rows), len(p['sourceQueries']))
        for exported, expected in zip(rows, p['sourceQueries']):
            self.assertEqual(exported['report'], expected['report'])
            self.assertEqual(exported['query name'], expected['queryName'])
            self.assertEqual(exported['query m code'], "'-1" if expected['mCode'] == '-1' else expected['mCode'])
