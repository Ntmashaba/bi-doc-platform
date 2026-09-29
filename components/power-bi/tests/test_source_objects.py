import copy
import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_column_usage import raw_model, report_fixture
from pbidocgen.m_sources import Tracer, materialize
from pbidocgen.sql_sources import extract_sql_objects
from pbidocgen.model_parser import parse_model
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.linker import link


class SQLObjectTests(unittest.TestCase):
    def objects(self, sql):
        return {tuple(p) for p in extract_sql_objects(sql)[0]}

    def test_joins_comma_sources_and_subqueries(self):
        sql = 'SELECT a.ID FROM dbo.Orders a JOIN crm.Customers b ON a.ID=b.ID, audit.Log l WHERE EXISTS(SELECT 1 FROM archive.OldOrders z)'
        self.assertEqual(self.objects(sql), {('dbo','Orders'),('crm','Customers'),('audit','Log'),('archive','OldOrders')})

    def test_cte_scope_nested_shadowing_and_statement_boundaries(self):
        sql = 'WITH x AS(SELECT * FROM dbo.A), y AS(SELECT * FROM x) SELECT * FROM y JOIN dbo.x ON 1=1; SELECT * FROM x'
        self.assertEqual(self.objects(sql), {('dbo','A'),('dbo','x'),('x',)})
        nested = 'SELECT * FROM (WITH x AS(SELECT * FROM dbo.A) SELECT * FROM x) q JOIN x ON 1=1'
        self.assertEqual(self.objects(nested), {('dbo','A'),('x',)})
        recursive = 'WITH RECURSIVE r AS (SELECT * FROM base.A UNION ALL SELECT a.* FROM base.B a JOIN r ON 1=1) SELECT * FROM r'
        self.assertEqual(self.objects(recursive), {('base','A'),('base','B')})

    def test_quoted_objects_literals_comments_and_extract(self):
        sql = '''SELECT EXTRACT(YEAR FROM t.dt), 'FROM fake.Table', q'[JOIN fake.Other's]' FROM "HR"."Odd.Name" t -- JOIN wrong.A
        JOIN [db].[s].[Order]]Lines] l ON 1=1 /* FROM wrong.B */'''
        self.assertEqual(self.objects(sql), {('HR','Odd.Name'),('db','s','Order]Lines')})

    def test_functions_dynamic_and_malformed_sql_are_not_physical_tables(self):
        objects, issues = extract_sql_objects("SELECT * FROM dbo.Real JOIN OPENQUERY(remote, 'SELECT * FROM secret.Table') q ON 1=1")
        self.assertEqual(objects, [['dbo','Real']])
        self.assertTrue(issues)
        objects, issues = extract_sql_objects("EXEC('SELECT * FROM hidden.Table')")
        self.assertEqual(objects, [])
        self.assertTrue(issues)
        self.assertEqual(extract_sql_objects('SELECT * FROM "broken')[0], [])

    def test_qualified_missing_schema_and_duplicate_aliases(self):
        self.assertEqual(self.objects('SELECT * FROM remote.Db.dbo.T t JOIN remote.Db.dbo.T u ON 1=1 JOIN OtherDb..U x ON 1=1'), {('remote','Db','dbo','T'),('OtherDb','','U')})


class MSourceTests(unittest.TestCase):
    def rows(self, code, definitions=None):
        return materialize(Tracer(definitions).trace(code))

    def test_navigation_uses_actual_reachable_connection(self):
        code = 'let Unused=Sql.Database("wrong","wrong"), S=Sql.Database("right","Warehouse"), T=S{[Item="Orders",Schema="sales"]}[Data] in Table.SelectRows(T, each [Amount]>0)'
        rows = self.rows(code)
        self.assertEqual([(r['server'],r['database'],r['schema'],r['object'],r['status']) for r in rows], [('right','Warehouse','sales','Orders','Resolved')])

    def test_oracle_and_teradata_native_and_navigation(self):
        oracle = self.rows('let S=Oracle.Database("ora:1521/service"), H=S{[Schema="HR"]}[Data] in H{[Name="EMPLOYEES"]}[Data]')
        self.assertEqual((oracle[0]['sourceType'],oracle[0]['database'],oracle[0]['schema'],oracle[0]['object']), ('Oracle','service','HR','EMPLOYEES'))
        td = self.rows('Teradata.Database("td", [Query="SELECT * FROM Finance.Accounts JOIN Audit.Events ON 1=1"])')
        self.assertEqual({(r['database'],r['object']) for r in td}, {('Finance','Accounts'),('Audit','Events')})
        nav = self.rows('let S=Teradata.Database("td") in S{[Schema="Finance",Item="Accounts"]}[Data]')
        self.assertEqual((nav[0]['database'],nav[0]['schema'],nav[0]['object']), ('Finance','','Accounts'))
        nested = self.rows('let S=Teradata.Database("td"), DB=S{[Schema="Finance"]}[Data] in DB{[Name="Accounts"]}[Data]')
        self.assertEqual((nested[0]['database'],nested[0]['schema'],nested[0]['object']), ('Finance','','Accounts'))

    def test_parameters_concatenation_meta_and_referenced_original_code(self):
        defs = {'Server': '"SQL01" meta [IsParameterQuery=true]', 'DB': '"Warehouse"', 'Stage': 'Sql.Database(Server, DB)'}
        code = 'let S=Stage, Q="SELECT * FROM dbo." & "Orders" in Value.NativeQuery(S,Q)'
        rows = self.rows(code, defs)
        self.assertEqual((rows[0]['server'],rows[0]['database'],rows[0]['object']), ('SQL01','Warehouse','Orders'))
        self.assertIn(defs['Stage'], rows[0]['referencedM'])
        self.assertIn(defs['Server'], rows[0]['referencedM'])

    def test_m_strings_comments_quoted_step_names_and_escapes(self):
        rows = self.rows('let #"Step // One"=Sql.Database("SQL01", "db", [Query="SELECT *#(lf)FROM dbo.T WHERE x=""FROM not_a_source"""]) /* nested /* comment */ end */ in #"Step // One"')
        self.assertEqual(rows[0]['object'], 'T')
        self.assertIn('\n', rows[0]['sql'])
        self.assertFalse(any(r['object']=='not_a_source' for r in rows))

    def test_multiple_connections_are_not_cross_associated(self):
        rows = self.rows('let A=Sql.Database("one","db1",[Query="SELECT * FROM dbo.A"]), B=Oracle.Database("two/service",[Query="SELECT * FROM HR.B"]) in Table.Combine({A,B})')
        self.assertEqual({(r['sourceType'],r['server'],r['database'],r['object']) for r in rows}, {('SQL Server','one','db1','A'),('Oracle','two/service','service','B')})

    def test_unresolved_sql_cycles_and_unsupported_function_are_visible(self):
        rows = self.rows('Sql.Database("sql","db",[Query=RuntimeSql])')
        self.assertEqual(rows[0]['status'], 'Unresolved')
        self.assertEqual(rows[0]['sql'], '')
        self.assertTrue(self.rows('Stage', {'Stage': 'Other', 'Other': 'Stage'})[0]['notes'])
        rows = self.rows('let S=Sql.Database("sql","db",[Query="SELECT * FROM dbo.T"]) in CustomFunction(S)')
        self.assertTrue(any(r['status']=='Unresolved' for r in rows))
        self.assertTrue(any(r['object']=='T' and r['status']=='Partial' for r in rows))

    def test_known_and_dynamic_native_queries_keep_both_sources(self):
        rows = self.rows('let A=Sql.Database("one","db1",[Query="SELECT * FROM dbo.A"]), B=Sql.Database("two","db2",[Query=RuntimeSql]) in Table.Combine({A,B})')
        self.assertTrue(any(r['server']=='two' and r['status']=='Unresolved' for r in rows))
        self.assertTrue(any(r['server']=='one' and r['object']=='A' for r in rows))

    def test_sql_database_context_changes_do_not_claim_original_database(self):
        rows = self.rows('Sql.Database("sql","initial",[Query="USE OtherDb; SELECT * FROM dbo.T"])')
        row = next(r for r in rows if r['object']=='T')
        self.assertEqual(row['database'], '')
        self.assertEqual(row['status'], 'Partial')

    def test_odbc_whitelists_identity_and_marks_external_dsn(self):
        rows = self.rows('Odbc.Query("Driver={SQL Server};Server=sql;Database=db;UID=reader;PWD=secret", "SELECT * FROM dbo.T")')
        self.assertEqual((rows[0]['sourceType'],rows[0]['server'],rows[0]['database']), ('SQL Server','sql','db'))
        self.assertNotIn('secret', repr(rows))
        rows = self.rows('Odbc.Query("DSN=Finance", "SELECT * FROM ledger")')
        self.assertEqual(rows[0]['status'], 'Partial')
        self.assertTrue(any('DSN' in n for n in rows[0]['notes']))


class SourceObjectInventoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def payload(self, report=True):
        raw = raw_model()
        code = 'let S=Sql.Database("sql","Warehouse",[Query="WITH x AS (SELECT * FROM sales.Orders) SELECT * FROM x JOIN crm.Customers c ON 1=1 JOIN sales.Orders o ON 1=1"]) in S'
        raw['model']['tables'][0]['partitions'][0]['source']['expression'] = code
        path = self.root / 'model.bim'
        path.write_text(json.dumps(raw))
        m = parse_model(path)
        r = report_fixture() if report else None
        return build_payload(m, r, link(m,r) if r else None, 'Objects'), code

    def test_rows_are_deduplicated_by_object_and_stable_page(self):
        p, code = self.payload()
        rows = [r for r in p['sourceObjects'] if r['table']=='Sales']
        # Two real pages plus one bookmark/unassigned scope for the same table.
        self.assertEqual(len(rows), 6)
        self.assertEqual({(r['pageId'],r['object']) for r in rows}, {(page,obj) for page in ('p1','p2','') for obj in ('Orders','Customers')})
        self.assertTrue(all(r['originalM']==code and r['sql'].startswith('WITH x') for r in rows))
        self.assertTrue(all(r['report']=='Sales' for r in rows))

    def test_model_only_keeps_unknown_page_scope(self):
        p, _ = self.payload(False)
        self.assertTrue(all(r['pageId']=='' for r in p['sourceObjects']))
        self.assertTrue(all(r['report']=='Not supplied' for r in p['sourceObjects']))

    @unittest.skipUnless(shutil.which('node'), 'Node needed for generated CSV checks')
    def test_generated_sources_csv_keeps_full_code_and_page_filter(self):
        p, _ = self.payload()
        next(r for r in p['sourceObjects'] if r['table']=='Sales')['originalM'] += '\n// ' + ('x' * 20000) + '\n// <script>marker</script>'
        for row in p['sourceObjects']:
            row['referencedM'] = '// Referenced query: Stage\r\nlet\n\tS = Sql.Database("server", "db")\nin S'
            row['server'] = 'server,with\ttab and "quotes"'
        html = render_html(p, self.root / 'objects.html')
        target = self.root / 'objects.csv'
        result = subprocess.run(['node', str(Path(__file__).with_name('check_source_objects.cjs')), str(html), str(target)], text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        with target.open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        source_rows=[r for r in p['sourceObjects'] if r['table']=='Sales']
        self.assertEqual(len(rows),len(source_rows))
        self.assertEqual(rows[0]['Original M code'],source_rows[0]['originalM'])
        self.assertEqual(rows[0]['Extracted SQL'],source_rows[0]['sql'])

        self.assertEqual(rows[0]['Referenced M code'], source_rows[0]['referencedM'])
        with Path(str(target)+'.no-code.csv').open(encoding='utf-8-sig', newline='') as f:
            no_code = list(csv.DictReader(f))
        expected = [r for r in rows if r['Page ID']=='p2']
        excluded = {'Original M code', 'Extracted SQL', 'Referenced M code'}
        self.assertEqual(no_code, [{k:v for k,v in r.items() if k not in excluded} for r in expected])
        self.assertEqual(len(no_code), 2)
        self.assertEqual(no_code[0]['Server / connection'], source_rows[0]['server'])
        self.assertNotIn('// Referenced query:', Path(str(target)+'.no-code.csv').read_text())
