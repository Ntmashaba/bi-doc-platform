import csv
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pbidocgen.primary_sources import build_primary_sources
from pbidocgen.source_objects import build_source_objects
from pbidocgen.model_parser import parse_model
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.linker import link
from test_column_usage import raw_model, report_fixture


class PrimarySourceTests(unittest.TestCase):
    def inventory(self, queries, definitions=None, report=True):
        model = dict(tables=[dict(name=name, partitions=[dict(name='Import', type='m', expression=code)])
                            for name, code in queries.items()],
                     expressions=[dict(name=name, kind='m', expression=code) for name, code in (definitions or {}).items()])
        usages = dict(tablePages=[dict(table=name, page='Same page name', pageId=pid, scope='Report page', usage='Direct')
                                 for name in queries for pid in ('p1', 'p2')]) if report else None
        r = dict(name='Report') if report else None
        return build_primary_sources(model, r, build_source_objects(model, r, usages))

    def test_secondary_chain_tracks_connection_owner_not_parameters(self):
        data = self.inventory({'Final': 'Table.SelectColumns(Clean, {"ID"})'}, {
            'Server': '"sql"', 'Stage': 'Sql.Database(Server,"db",[Query="SELECT * FROM dbo.Orders"])',
            'Clean': 'Table.Distinct(Stage)'})
        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(data['unresolved'], [])
        for row in data['rows']:
            self.assertEqual(row['primaryQueries'], ['Stage'])
            self.assertEqual(row['consumingQueries'], ['Final'])
            self.assertEqual((row['server'], row['database'], row['schema'], row['object']), ('sql', 'db', 'dbo', 'Orders'))
        self.assertEqual({r['pageId'] for r in data['rows']}, {'p1', 'p2'})

    def test_mixed_query_preserves_each_connection_owner(self):
        data = self.inventory({'Final': 'let Local=Oracle.Database("host/service",[Query="SELECT * FROM HR.People"]) in Table.Combine({Stage,Local})'},
                              {'Stage': 'Teradata.Database("td",[Query="SELECT * FROM Warehouse.Sales"])'})
        self.assertEqual({(r['server'], r['object'], tuple(r['primaryQueries'])) for r in data['rows']},
                         {('host/service', 'PEOPLE', ('Final',)), ('td', 'Sales', ('Stage',))})  # Oracle folds unquoted names)

    def test_group_same_external_object_across_queries_and_keep_pages(self):
        code = 'Sql.Database("sql","db",[Query="SELECT * FROM dbo.Orders"])'
        data = self.inventory({'A': 'Stage', 'B': 'Stage', 'C': code}, {'Stage': code})
        self.assertEqual(len(data['rows']), 2)
        for row in data['rows']:
            self.assertEqual(row['primaryQueries'], ['C', 'Stage'])
            self.assertEqual(row['consumingQueries'], ['A', 'B', 'C'])
            self.assertEqual(row['tables'], ['A', 'B', 'C'])

    def test_unsupported_and_cyclic_queries_are_coverage_gaps(self):
        data = self.inventory({'Final': 'Table.Combine({Stage,Loop,CustomConnector.Contents("https://example.invalid")})'}, {
            'Stage': 'Sql.Database("sql","db",[Query="SELECT * FROM dbo.Orders"])', 'Loop': 'Loop'})
        self.assertEqual(len(data['rows']), 4)
        self.assertEqual(len(data['unresolved']), 2)
        self.assertTrue(all(r['status']=='Partial' for r in data['rows'] if r['sourceType']!='Unknown'))
        self.assertTrue(all(r['queryName']=='Final' for r in data['unresolved']))

    def test_dynamic_sql_retains_connection_without_inventing_object(self):
        data = self.inventory({'Final': 'Sql.Database("sql","db",[Query=RuntimeQuery])'})
        self.assertEqual(len(data['rows']), 2)
        self.assertTrue(all(r['server']=='sql' and r['object']=='' and r['status']=='Unresolved' for r in data['rows']))

    def test_unconsumed_shared_source_and_model_only_have_no_invented_page(self):
        data = self.inventory({}, {'Unused': 'Oracle.Database("host/service",[Query="SELECT * FROM HR.People"])',
                                   'Parameter': '"not a data source"'})
        self.assertEqual(len(data['rows']), 1)
        self.assertEqual(data['rows'][0]['primaryQueries'], ['Unused'])
        self.assertEqual(data['rows'][0]['pageId'], '')
        self.assertEqual(data['rows'][0]['pageScope'], 'No model consumer resolved')
        self.assertEqual(data['rows'][0]['consumingQueries'], [])
        data = self.inventory({'Final': 'Sql.Database("sql","db")'}, report=False)
        self.assertEqual(data['rows'][0]['report'], 'Not supplied')
        self.assertEqual(data['rows'][0]['pageId'], '')

    @unittest.skipUnless(shutil.which('node'), 'Node required for actual generated CSV checks')
    def test_primary_export_has_one_physical_line_per_record_and_no_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = raw_model()
            raw['model']['tables'][0]['partitions'][0]['source']['expression'] = 'Stage'
            raw['model']['expressions'] = [dict(name='Stage', kind='m', expression='Sql.Database("sql","db",[Query="SELECT * FROM dbo.Orders"])')]
            raw['model']['expressions'] += [dict(name='Dormant', kind='m', expression='File.Contents("C:\\Dormant.csv")'), dict(name='Custom', kind='m', expression='CustomPlatform.Fetch("account")')]
            path = root / 'model.bim'
            path.write_text(json.dumps(raw))
            model, report = parse_model(path), report_fixture()
            p = build_payload(model, report, link(model, report), 'Primary')
            for row in p['primarySources']['rows']:
                row['server'] = 'sql, "quoted"\tserver\r\nline\u2028break'
                row['originalM'] = '// Referenced query: CODE_MUST_NOT_LEAK'
                row['evidence'] = 'CODE_MUST_NOT_LEAK'
            html = render_html(p, root / 'primary.html')
            target = root / 'primary.csv'
            result = subprocess.run(['node', str(Path(__file__).with_name('check_primary_sources.cjs')), str(html), str(target)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            with target.open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['Page ID'], 'p2')
            self.assertEqual(rows[0]['Connection queries'], 'Stage')
            self.assertEqual(rows[0]['Consuming model queries'], 'Sales')
            self.assertEqual(rows[0]['Server / connection'], 'sql, "quoted" server line break')
            self.assertEqual(len(rows[0]), 24)
            self.assertEqual(rows[0]['Configured storage modes'], 'import')
            self.assertEqual(rows[0]['Reporting usage'], 'Potential reporting dependency')
            with Path(str(target)+'.unassigned.csv').open(encoding='utf-8-sig', newline='') as f:
                unassigned=list(csv.DictReader(f))
            self.assertEqual(len(unassigned), 3)
            self.assertEqual({r['Reporting usage'] for r in unassigned}, {'Report scope only', 'No model consumer found', 'Usage unresolved'})
            self.assertTrue(all(r['Page ID']=='' for r in unassigned))
            text = target.read_text(encoding='utf-8-sig')
            self.assertEqual(len(text.splitlines()), 2)
            self.assertNotIn('CODE_MUST_NOT_LEAK', text)
            self.assertNotIn('// Referenced query:', text)
            self.assertNotIn('\t', text)
            for row in rows:
                self.assertIsNone(re.search(r'[\r\n\u2028\u2029]', ''.join(row.values())))
