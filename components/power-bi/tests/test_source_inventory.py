import json
import unittest

import test_column_usage as fixtures
from test_column_usage import raw_model, report_fixture
from pbidocgen.model_parser import extract_m_source
from pbidocgen.renderer import build_payload
from pbidocgen.source_inventory import enrich_source


class SourceParsingTests(unittest.TestCase):
    def source(self, m):
        return enrich_source(extract_m_source(m, "m"), m, "m")

    def test_navigation_schema_order_and_view_kind(self):
        source = self.source('let S=Sql.Database("sql-host","Warehouse"), T=S{[Item="OrderView",Kind="View",Schema="reporting"]}[Data] in T')
        self.assertEqual((source["server"], source["database"], source["schema"], source["object"], source["objectType"]),
                         ("sql-host", "Warehouse", "reporting", "OrderView", "View"))
        source = self.source('let S=Sql.Database("sql-host","Warehouse"), T=S{[Schema="dbo",Item="vwOrders"]}[Data] in T')
        self.assertEqual(source["objectType"], "Table or view")

    def test_query_option_preserves_sql_quotes_and_newlines(self):
        source = self.source('let S = Sql.Database("sql-host", "Warehouse", [Query="select ""Id"", Amount#(cr,lf)from dbo.Orders", CommandTimeout=#duration(0,0,5,0)]) in S')
        self.assertEqual(source["query"], 'select "Id", Amount\r\nfrom dbo.Orders')
        self.assertTrue(source["nativeQuery"])
        self.assertEqual(source["queryKind"], "SQL")

    def test_native_nested_target_and_dynamic_query(self):
        source = self.source('let S=Value.NativeQuery(Sql.Database("server", "db"), "select COALESCE(x, 0) from dbo.T", null, [EnableFolding=true]) in S')
        self.assertEqual((source["server"], source["database"]), ("server", "db"))
        self.assertEqual(source["query"], "select COALESCE(x, 0) from dbo.T")
        m='let S=Sql.Database(ServerParameter, "db", [Query="select * from " & TableName]) in S'
        source = self.source(m)
        self.assertIsNone(source["server"])
        self.assertEqual(source["query"], m)
        self.assertEqual(source["queryKind"], "Power Query (M)")

    def test_databases_navigation_and_multiple_connections(self):
        source = self.source('let S=Sql.Databases("server"), D=S{[Name="Warehouse"]}[Data], T=D{[Schema="dbo",Item="Orders"]}[Data] in T')
        self.assertEqual(source["database"], "Warehouse")
        self.assertEqual(source["object"], "Orders")
        source = self.source('let A=Sql.Database("one", "a"), B=Sql.Database("two", "b"), X=A{[Schema="dbo",Item="Orders"]}[Data] in X')
        self.assertIsNone(source["server"])
        self.assertIsNone(source["database"])
        self.assertIsNone(source["object"])
        self.assertTrue(any("Multiple SQL" in s for s in source["notes"]))

    def test_comments_and_sql_strings_do_not_create_connections(self):
        source = self.source('// Sql.Database("wrong","wrong")\nlet S=Sql.Database("right", "db", [Query="select \'Sql.Database(1,2)\' as x"]) in S')
        self.assertEqual(source["server"], "right")
        self.assertEqual(source["query"], "select 'Sql.Database(1,2)' as x")


class SourceInventoryTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing model loader fixture, without inheriting its tests.
        self.fixture = fixtures.ColumnUsageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_partitions_report_usage_and_no_partition_table(self):
        raw = raw_model()
        raw["model"]["tables"][0]["partitions"].append({"name": "Archive", "source": {"type": "m", "expression":
            'Sql.Database("archive-server", "ArchiveDB", [Query="select * from dbo.History"])'}})
        model = self.fixture.model(raw)
        payload = build_payload(model, report_fixture(), None, "title")
        rows = payload["tableSources"]
        self.assertEqual(len(rows), 7)  # two pages + bookmark scope, per partition; one unused table
        self.assertEqual(rows[0]["report"], "Sales")
        self.assertEqual(rows[0]["object"], "dbo.Orders")
        self.assertEqual({r["pageId"] for r in rows if r["table"] == "Sales"}, {"p1", "p2", ""})
        archive = next(r for r in rows if r["partition"] == "Archive" and r["pageId"] == "p2")
        self.assertIn("Direct", archive["usage"])
        self.assertEqual(archive["query"], "select * from dbo.History")
        self.assertEqual(archive["server"], "archive-server")
        dim = next(r for r in rows if r["table"] == "Dim")
        self.assertEqual(dim["partition"], "")
        self.assertEqual(dim["usage"], "No page usage detected")
        model_only = build_payload(model, None, None, "title")["tableSources"]
        self.assertTrue(all(r["usage"] == "Usage unknown" for r in model_only))
        self.assertEqual(build_payload(None, report_fixture(), None, "title")["tableSources"], [])

    def test_legacy_query_partition_resolves_named_connection(self):
        raw = raw_model()
        raw["model"]["dataSources"] = [{"name": "SQL", "connectionString":
            "Provider=SQLNCLI;Data Source=my-host;Initial Catalog=my-db;User ID=user;Password=do-not-expose;"}]
        raw["model"]["tables"][0]["partitions"] = [{"name": "SQL", "source": {
            "type": "query", "query": ["SELECT *", "FROM dbo.SourceView"], "dataSource": "SQL"}}]
        rows = build_payload(self.fixture.model(raw), report_fixture(), None, "title")["tableSources"]
        self.assertEqual(rows[0]["query"], "SELECT *\nFROM dbo.SourceView")
        self.assertEqual((rows[0]["server"], rows[0]["database"]), ("my-host", "my-db"))
        self.assertNotIn("do-not-expose", json.dumps(rows))


if __name__ == '__main__':
    unittest.main()
