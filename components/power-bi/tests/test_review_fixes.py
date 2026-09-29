import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_column_usage import raw_model, report_fixture, field
from pbidocgen.model_parser import parse_model
from pbidocgen.report_parser import parse_report
from pbidocgen.renderer import build_payload
from pbidocgen.linker import link
from pbidocgen.dax_lexer import mask_dax


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def payload(self, raw, report=None):
        path = self.root / 'model.bim'
        path.write_text(json.dumps(raw))
        m = parse_model(path)
        r = report if report is not None else report_fixture()
        return build_payload(m, r, link(m, r), 'Review fixes')

    def test_measure_detail_rows_protect_column_and_nested_measure(self):
        raw = raw_model()
        raw['model']['tables'][0]['measures'][0]['detailRowsDefinition'] = {'expression': 'SELECTCOLUMNS(Sales,"X",Sales[Unused]+[Total])'}
        p = self.payload(raw)
        row = next(r for r in p['columns']['rows'] if r['column'] == 'Unused')
        self.assertEqual(row['decision'], 'Keep')
        self.assertTrue(any('detailRowsDefinition' in d for d in row['modelDependencies']))
        self.assertIn('detailRowsDefinition', p['model']['measures'][0])

    def test_lexer_preserves_identifiers_and_ignores_real_comments_strings(self):
        raw = raw_model()
        raw['model']['tables'].append({'name': 'Rates//USD', 'columns': [{'name': 'Value--x'}, {'name': "Q/*x*/"}]})
        raw['model']['tables'][0]['measures'].append({'name': 'Rate', 'expression': "SUM('Rates//USD'[Value--x])+SUM('Rates//USD'[Q/*x*/]) // Sales[Unused]\n + LEN(\"Sales[Unused]\")"})
        r = report_fixture()
        r['pages'][0]['visuals'][0]['fields'].append(field('Rate', 'measure'))
        p = self.payload(raw, r)
        rows = [r for r in p['columns']['rows'] if r['table'] == 'Rates//USD']
        self.assertTrue(all(r['usedInReport'] == 'Yes' and r['pageId'] == 'p1' for r in rows))
        self.assertEqual(next(r for r in p['columns']['rows'] if r['column'] == 'Unused')['decision'], 'Deletion candidate')
        self.assertIn("'O''Brien//USD'[A]]B]", mask_dax("'O''Brien//USD'[A]]B] + \"text//\" /*ignored*/"))
        self.assertNotIn('ignored', mask_dax('/*ignored*/ 1'))

    def test_parameter_dependencies_are_possible_on_each_page_every_inventory(self):
        raw = raw_model()
        raw['model']['tables'].append({'name': 'Param', 'columns': [{'name': 'Choice', 'type': 'calculatedTableColumn'}], 'partitions': [{'name': 'Param', 'source': {'type': 'calculated', 'expression': '{("Total",NAMEOF(Sales[Total]),0)}'}}]})
        r = report_fixture()
        r['bookmarks'] = []
        for page in r['pages']:
            page['visuals'][0]['fields'] = [field('Choice', table='Param')]
        p = self.payload(raw, r)
        rows = [x for x in p['columns']['rows'] if x['column'] == 'Amount']
        self.assertEqual({x['pageId'] for x in rows}, {'p1', 'p2'})
        self.assertTrue(all(x['usedInReport'] == 'Possible' and 'Possible' in x['pageUsage'] for x in rows))
        self.assertTrue(all('Sales[Total]' in x['pageMeasures'] for x in rows))
        self.assertEqual({x['pageId'] for x in p['tableSources'] if x['table'] == 'Sales' and 'Possible' in x['usage']}, {'p1', 'p2'})
        self.assertTrue(any(e['possible'] for e in p['columns']['dependencyGraph']['edges']))
        self.assertTrue(all(any(f['table'] == 'Sales' for f in page['feeds']) for page in p['report']['pages']))
        self.assertEqual(next(x for x in p['linked']['tableUsage'] if x['table'] == 'Sales')['usage'], 'possible')

    def report_dir(self):
        d = self.root / 'Test.Report' / 'definition'
        p = d / 'pages' / 'present'
        p.mkdir(parents=True)
        (d / 'report.json').write_text('{"name":"Report"}')
        (p / 'page.json').write_text('{"displayName":"Present"}')
        return d, p

    def test_missing_declared_page_blocks_candidates(self):
        d, _ = self.report_dir()
        (d / 'pages' / 'pages.json').write_text('{"pageOrder":["present","missing"]}')
        report = parse_report(d)
        self.assertTrue(any('missing' in w['message'] for w in report['warnings']))
        p = self.payload(raw_model(), report)
        self.assertEqual(next(x for x in p['columns']['rows'] if x['column'] == 'Unused')['decision'], 'Review')

    def test_malformed_top_level_and_nested_report_shapes_are_diagnosed(self):
        d, page = self.report_dir()
        for invalid in ([1], {'filterConfig': {'filters': 'invalid'}}, {'Expression': {'Hierarchy': 'bad'}}):
            with self.subTest(invalid=invalid):
                (page / 'page.json').write_text(json.dumps(invalid))
                report = parse_report(d)
                self.assertTrue(report['warnings'])
                self.assertEqual(report['pages'], [])
        (page / 'page.json').write_text('{"displayName":"Page"}')
        v = page / 'visuals' / 'v'
        v.mkdir(parents=True)
        (v / 'visual.json').write_text('{"visual": []}')
        self.assertTrue(any(w['category'] == 'Unreadable visual' for w in parse_report(d)['warnings']))
        (d / 'pages' / 'pages.json').write_text('[1]')
        self.assertTrue(any('pages.json' in w['message'] for w in parse_report(d)['warnings']))

    def test_model_shape_error_is_value_error(self):
        for raw in ([], {'model': []}, {'model': {'tables': [1]}}):
            with self.subTest(raw=raw):
                path = self.root / 'invalid.bim'
                path.write_text(json.dumps(raw))
                with self.assertRaises(ValueError):
                    parse_model(path)

    def test_tmdl_preserves_detail_rows_and_calculation_metadata(self):
        d = self.root / 'Demo.SemanticModel' / 'definition'
        (d / 'tables').mkdir(parents=True)
        (d / 'model.tmdl').write_text('model Model\n')
        (d / 'tables' / 'T.tmdl').write_text('''table T
    column X
        dataType: int64
    measure M = 1
        detailRowsDefinition = SELECTCOLUMNS(T, "X", T[X])
    calculationGroup
        precedence: 10
        calculationItem Double = SELECTEDMEASURE()*2
            formatStringDefinition = "0.00"
''')
        m = parse_model(d.parent)
        self.assertIn('SELECTCOLUMNS', m['measures'][0]['detailRowsDefinition']['expression'])
        group = m['tables'][0]['calculationGroupDefinition']
        self.assertEqual(str(group['precedence']), '10')
        self.assertEqual(group['calculationItems'][0]['formatStringDefinition']['expression'], '"0.00"')
        self.assertTrue(any('detailRowsDefinition' in e['label'] for e in m['dependencyExpressions']))
