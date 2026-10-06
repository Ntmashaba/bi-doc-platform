"""Analysis Services tabular models: model.bim and SSMS scripts at compatibility level 1200 and later.

XML definitions (levels 1100 and 1103) are in test_assl_reader.py.

The shapes are the ones in Microsoft's sample models (github.com/microsoft/Analysis-Services): a structured
data source named in Power Query as #"name", a provider data source read by a partition that is just
{"query", "dataSource"} with no "type", and a CREATE / CREATE OR REPLACE script around the same database.
"""
import json
import tempfile
import unittest
from pathlib import Path

from pbidocgen import data_sources
from pbidocgen.model_parser import parse_model, unwrap_tmsl_script
from pbidocgen.source_objects import build_source_objects

STRUCTURED = {
    "type": "structured", "name": "SQL/sqlprod02;FinanceDW",
    "connectionDetails": {"protocol": "tds", "address": {"server": "sqlprod02", "database": "FinanceDW"},
                          "authentication": None, "query": None},
    "credential": {"AuthenticationKind": "UsernamePassword", "kind": "SQL", "path": "sqlprod02;FinanceDW",
                   "Username": "svc_finance_reader", "EncryptConnection": True},
}
PROVIDER = {
    "name": "SqlServer sqlprod01 SalesDW",
    "connectionString": "Provider=SQLNCLI11;Data Source=sqlprod01;Initial Catalog=SalesDW;User ID=svc_sales_reader;"
                        "Password=hunter2;Persist Security Info=true",
    "impersonationMode": "impersonateAccount", "account": "CORP\\svc_ssas",
}
SECRETS = ("svc_finance_reader", "svc_sales_reader", "hunter2", "svc_ssas", "UsernamePassword")


def column(name="Amount"):
    return {"name": name, "dataType": "double", "sourceColumn": name}


def m_table(name, *lines):
    # Expressions as a list of lines, the way SSDT and SSMS write them.
    return {"name": name, "columns": [column()], "partitions": [{"name": name, "source": {"type": "m", "expression": list(lines)}}]}


def query_table(name, data_source, *queries):
    # No "type": Analysis Services writes a provider partition as query + dataSource only.
    return {"name": name, "columns": [column()],
            "partitions": [{"name": f"{name} {i}", "dataView": "full", "source": {"query": q, "dataSource": data_source}}
                           for i, q in enumerate(queries, 1)]}


def database(tables, sources, level=1500, expressions=None):
    model = {"culture": "en-US", "dataSources": sources, "tables": tables}
    if expressions:
        model["expressions"] = expressions
    return {"name": "Finance", "compatibilityLevel": level, "model": model}


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def load(self, doc, name="Model.bim", encoding="utf-8"):
        path = self.tmp / name
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=2), encoding=encoding)
        return parse_model(path)

    def objects(self, model):
        return {(r["table"], r["partition"], r["sourceType"], r["server"], r["database"], r["schema"], r["object"], r["status"])
                for r in build_source_objects(model, None, None)}


class StructuredDataSources(Case):
    def test_navigation_from_a_named_data_source_resolves_to_its_server_and_table(self):
        model = self.load(database([m_table(
            "GL", "let", '    Source = #"SQL/sqlprod02;FinanceDW",',
            '    fin_GL = Source{[Schema="fin",Item="GeneralLedger"]}[Data],',
            '    #"Removed Columns" = Table.RemoveColumns(fin_GL,{"Legacy"})', "in", '    #"Removed Columns"')], [STRUCTURED]))
        self.assertEqual(model["compatibilityLevel"], 1500)
        source = model["tables"][0]["partitions"][0]["source"]
        self.assertEqual((source["sourceType"], source["server"], source["database"], source["schema"], source["object"]),
                         ("SQL Server", "sqlprod02", "FinanceDW", "fin", "GeneralLedger"))
        self.assertEqual(source["traceStatus"], "Resolved")
        self.assertEqual(source["label"], "SQL Server · sqlprod02 / FinanceDW")
        self.assertEqual(self.objects(model),
                         {("GL", "GL", "SQL Server", "sqlprod02", "FinanceDW", "fin", "GeneralLedger", "Resolved")})

    def test_a_native_query_against_a_data_source_lists_every_table_in_the_sql(self):
        model = self.load(database([m_table(
            "Budget", "let", '    Source = Value.NativeQuery(#"SQL/sqlprod02;FinanceDW", '
            '"select b.* from fin.Budget b join fin.CostCentre c on c.id = b.cc")', "in", "    Source")], [STRUCTURED]))
        self.assertEqual({row[2:] for row in self.objects(model)},
                         {("SQL Server", "sqlprod02", "FinanceDW", "fin", "Budget", "Resolved"),
                          ("SQL Server", "sqlprod02", "FinanceDW", "fin", "CostCentre", "Resolved")})

    def test_a_table_named_like_its_data_source_reads_the_data_source_not_itself(self):
        source = dict(STRUCTURED, name="Sales")
        model = self.load(database([m_table(
            "Sales", "let", '    Source = #"Sales",', '    T = Source{[Schema="dbo",Item="FactSales"]}[Data]', "in", "    T")], [source]))
        self.assertEqual(self.objects(model),
                         {("Sales", "Sales", "SQL Server", "sqlprod02", "FinanceDW", "dbo", "FactSales", "Resolved")})

    def test_a_shared_expression_wins_over_a_data_source_of_the_same_name(self):
        expressions = [{"name": STRUCTURED["name"], "kind": "m", "expression": 'Sql.Database("override", "Other")'}]
        model = self.load(database([m_table(
            "GL", "let", '    Source = #"SQL/sqlprod02;FinanceDW",', '    T = Source{[Schema="fin",Item="GL"]}[Data]', "in", "    T")],
            [STRUCTURED], expressions=expressions))
        self.assertEqual({row[3:5] for row in self.objects(model)}, {("override", "Other")})

    def test_a_file_data_source_is_named_by_its_file(self):
        source = {"type": "structured", "name": "File/Targets",
                  "connectionDetails": {"protocol": "file", "address": {"path": "\\\\share\\finance\\Targets.xlsx"}}}
        model = self.load(database([m_table(
            "Targets", "let", '    Source = Excel.Workbook(#"File/Targets", null, true),',
            '    Sheet = Source{[Item="Targets",Kind="Sheet"]}[Data]', "in", "    Sheet")], [source]))
        part = model["tables"][0]["partitions"][0]["source"]
        self.assertEqual(part["sourceType"], "Excel workbook")
        self.assertEqual(part["label"], "Excel workbook · Targets.xlsx")

    def test_unused_date_parameters_are_not_listed_as_an_unknown_source(self):
        # Microsoft's "AW Internet Sales" sample declares RangeStart and RangeEnd without using them.
        from pbidocgen.primary_sources import build_primary_sources
        expressions = [{"name": name, "kind": "m", "expression": [
            "let", f'    Source = #datetime(2013, 6, {day}, 0, 0, 0) meta [IsParameterQuery=true, Type="DateTime", '
            'IsParameterQueryRequired=true]', "in", "    Source"]} for name, day in (("RangeStart", 1), ("RangeEnd", 8))]
        model = self.load(database([m_table(
            "GL", "let", '    Source = #"SQL/sqlprod02;FinanceDW",', '    T = Source{[Schema="fin",Item="GL"]}[Data]', "in", "    T")],
            [STRUCTURED], expressions=expressions))
        primary = build_primary_sources(model, None, build_source_objects(model, None, None))
        self.assertEqual([(r["sourceType"], r["object"]) for r in primary["rows"]], [("SQL Server", "GL")])
        self.assertEqual(primary["unresolved"], [])

    def test_an_unmapped_protocol_stays_unresolved_instead_of_being_guessed(self):
        source = {"type": "structured", "name": "Lake",
                  "connectionDetails": {"protocol": "some-new-protocol", "address": {"server": "x"}}}
        model = self.load(database([m_table("T", "let", '    Source = #"Lake"', "in", "    Source")], [source]))
        self.assertIsNone(model["dataSources"][0]["expression"])
        self.assertEqual({row[-1] for row in self.objects(model)}, {"Unresolved"})

    def test_equivalent_power_query_escapes_quotes_and_m_escape_sequences(self):
        source = dict(STRUCTURED, connectionDetails={"protocol": "tds", "address": {"server": 'a"b#(lf)', "database": "d"}})
        self.assertIn('Sql.Database("a""b#(#)(lf)", "d")', data_sources.equivalent_m(source))
        model = self.load(database([m_table("T", "let", '    S = #"SQL/sqlprod02;FinanceDW"', "in", "    S")], [source]))
        self.assertEqual({row[3] for row in self.objects(model)}, {'a"b#(lf)'})


class ProviderDataSources(Case):
    def test_a_partition_without_a_type_is_read_as_its_sql_query(self):
        model = self.load(database([query_table(
            "Sales", PROVIDER["name"],
            " SELECT [dbo].[FactSales].* FROM [dbo].[FactSales] WHERE ([OrderDateKey] >= 20240101) ",
            "SELECT f.Amount, d.Region FROM dbo.FactSales2025 f JOIN ref.DimRegion d ON d.Id = f.RegionId")],
            [PROVIDER], level=1400))
        parts = model["tables"][0]["partitions"]
        self.assertEqual([p["type"] for p in parts], ["query", "query"])
        self.assertEqual({(p["source"]["sourceType"], p["source"]["server"], p["source"]["database"]) for p in parts},
                         {("SQL Server", "sqlprod01", "SalesDW")})
        self.assertEqual({row[1:] for row in self.objects(model)},
                         {("Sales 1", "SQL Server", "sqlprod01", "SalesDW", "dbo", "FactSales", "Resolved"),
                          ("Sales 2", "SQL Server", "sqlprod01", "SalesDW", "dbo", "FactSales2025", "Resolved"),
                          ("Sales 2", "SQL Server", "sqlprod01", "SalesDW", "ref", "DimRegion", "Resolved")})

    def test_the_provider_names_the_kind_of_database(self):
        cases = {"Provider=SQLNCLI11;Data Source=s": "SQL Server", "Provider=MSOLEDBSQL.1;Data Source=s": "SQL Server",
                 "Provider=OraOLEDB.Oracle;Data Source=ORAPRD": "Oracle", "Provider=TDOLEDB;Data Source=td": "Teradata",
                 "Provider=MSOLAP.8;Data Source=asazure://x": "Analysis Services",
                 "Data Source=s;Initial Catalog=d;Integrated Security=True": None, "": None}
        for connection, expected in cases.items():
            self.assertEqual(data_sources.provider_source_type(connection), expected, connection)

    def test_oracle_names_are_folded_to_upper_case_and_an_unknown_provider_keeps_the_generic_label(self):
        oracle = {"name": "ORA", "connectionString": "Provider=OraOLEDB.Oracle;Data Source=ORAPRD;User ID=svc_sales_reader;Password=hunter2"}
        plain = {"name": "Plain", "connectionString": "Data Source=srv;Initial Catalog=db;Integrated Security=True"}
        model = self.load(database([query_table("Tariff", "ORA", "select * from billing.tariff"),
                                    query_table("Other", "Plain", "select * from dbo.Other")], [oracle, plain], level=1200))
        rows = {row[0]: row[2:] for row in self.objects(model)}
        self.assertEqual(rows["Tariff"][:5], ("Oracle", "ORAPRD", "", "BILLING", "TARIFF"))
        self.assertEqual(rows["Other"], ("SQL query source", "srv", "db", "dbo", "Other", "Resolved"))

    def test_an_explicit_query_type_still_works(self):
        table = {"name": "T", "columns": [column()], "partitions": [
            {"name": "T", "source": {"type": "query", "dataSource": PROVIDER["name"], "query": "SELECT * FROM dbo.T"}}]}
        model = self.load(database([table], [PROVIDER]))
        self.assertEqual({row[2:] for row in self.objects(model)}, {("SQL Server", "sqlprod01", "SalesDW", "dbo", "T", "Resolved")})


class Credentials(Case):
    def test_nothing_that_authenticates_reaches_the_model_or_the_source_rows(self):
        model = self.load(database(
            [m_table("GL", "let", '    Source = #"SQL/sqlprod02;FinanceDW",', '    T = Source{[Schema="fin",Item="GL"]}[Data]', "in", "    T"),
             query_table("Sales", PROVIDER["name"], "SELECT * FROM dbo.FactSales")], [STRUCTURED, PROVIDER]))
        text = json.dumps([model, build_source_objects(model, None, None)])
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertEqual([(d["name"], d["kind"], d["sourceType"], d["server"], d["database"]) for d in model["dataSources"]],
                         [("SQL/sqlprod02;FinanceDW", "structured", "SQL Server", "sqlprod02", "FinanceDW"),
                          ("SqlServer sqlprod01 SalesDW", "provider", "SQL Server", "sqlprod01", "SalesDW")])


class SsmsScripts(Case):
    def tables(self):
        return [m_table("GL", "let", '    Source = #"SQL/sqlprod02;FinanceDW",', '    T = Source{[Schema="fin",Item="GL"]}[Data]', "in", "    T")]

    def expect_gl(self, model):
        self.assertEqual(model["compatibilityLevel"], 1500)
        self.assertEqual(self.objects(model), {("GL", "GL", "SQL Server", "sqlprod02", "FinanceDW", "fin", "GL", "Resolved")})

    def test_create_script_reads_like_the_model_bim(self):
        self.expect_gl(self.load({"create": {"database": database(self.tables(), [STRUCTURED])}}, "Finance.xmla"))

    def test_create_or_replace_and_alter_scripts(self):
        db = database(self.tables(), [STRUCTURED])
        self.expect_gl(self.load({"createOrReplace": {"object": {"database": "Finance"}, "database": db}}, "a.xmla"))
        self.expect_gl(self.load({"alter": {"object": {"database": "Finance"}, "database": db}}, "b.xmla"))

    def test_a_script_saved_as_utf16_or_with_a_bom(self):
        script = {"create": {"database": database(self.tables(), [STRUCTURED])}}
        self.expect_gl(self.load(script, "utf16.xmla", encoding="utf-16"))
        self.expect_gl(self.load(script, "bom.xmla", encoding="utf-8-sig"))

    def test_a_sequence_is_searched_for_the_database_it_defines(self):
        db = database(self.tables(), [STRUCTURED])
        script = {"sequence": {"operations": [{"refresh": {"type": "full", "objects": [{"database": "Other"}]}},
                                              {"createOrReplace": {"object": {"database": "Finance"}, "database": db}}]}}
        self.expect_gl(self.load(script, "seq.xmla"))

    def test_a_model_bim_is_left_alone(self):
        db = database(self.tables(), [STRUCTURED])
        self.assertIs(unwrap_tmsl_script(db), db)
        bare = {"tables": [], "create": "a model property that is not a command"}
        self.assertIs(unwrap_tmsl_script(bare), bare)

    def test_a_script_of_one_table_says_to_script_the_database(self):
        script = {"createOrReplace": {"object": {"database": "Finance", "table": "GL"}, "table": self.tables()[0]}}
        with self.assertRaisesRegex(ValueError, "does not define a whole database.*Script Database as"):
            self.load(script, "table.xmla")

    def test_a_processing_script_is_not_mistaken_for_an_empty_model(self):
        with self.assertRaisesRegex(ValueError, "'refresh' command, not a model definition"):
            self.load({"refresh": {"type": "full", "objects": [{"database": "Finance"}]}}, "process.xmla")
        with self.assertRaisesRegex(ValueError, "sequence does not define a database"):
            self.load({"sequence": {"operations": [{"refresh": {"type": "full", "objects": []}}]}}, "seq.xmla")

    def test_a_file_that_is_neither_json_nor_xml_keeps_the_json_error(self):
        with self.assertRaisesRegex(ValueError, "Could not parse"):
            self.load("not a model", "junk.bim")


if __name__ == "__main__":
    unittest.main()
