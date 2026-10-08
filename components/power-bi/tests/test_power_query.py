"""The Power Query inventory and view: one entry per query, read the same way from every file format."""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pbidocgen import input_limits, portable  # noqa: E402
from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.object_index import build_search_index  # noqa: E402
from pbidocgen.pbitools_folder import assemble  # noqa: E402
from pbidocgen.power_query import power_query_view  # noqa: E402
from pbidocgen.publication import CODE_WITHHELD, publication_of  # noqa: E402
from pbidocgen.renderer import build_payload, render_html  # noqa: E402
from pbidocgen.source_queries import build_source_queries, query_groups  # noqa: E402

SALES = ('let\n    Source = Sql.Database("srv", "dw"),\n    Orders = Source{[Schema="dbo",Item="Orders"]}[Data],\n'
         '    #"Kept Rows" = Table.SelectRows(Orders, each [Region] = Region),\n    Cleaned = fnClean(#"Kept Rows")\nin\n    Cleaned')
STAGE = 'let\n    Source = Csv.Document(Web.Contents(BaseUrl & "stage.csv"))\nin\n    Source'


def m(expression, **more):
    return {"name": more.pop("name", "p"), "source": {"type": "m", "expression": expression}, **more}


def model_doc():
    return {"model": {
        "name": "Shop",
        "annotations": [{"name": "PBI_QueryOrder", "value": '["BaseUrl","Region","Stage","Sales","Customers","fnClean"]'}],
        "queryGroups": [
            {"folder": "Staging", "description": "Read once, used twice.", "annotations": [{"name": "PBI_QueryGroupOrder", "value": "1"}]},
            {"folder": "Parameters", "annotations": [{"name": "PBI_QueryGroupOrder", "value": "0"}]}],
        "tables": [
            {"name": "Sales", "columns": [{"name": "Amount"}], "partitions": [m(SALES, name="Sales-1f")],
             "annotations": [{"name": "PBI_ResultType", "value": "Table"}]},
            {"name": "Customers", "columns": [{"name": "Id"}],
             "partitions": [m('let Source = Stage, Named = Table.RenameColumns(Source, {}) in Named', name="Customers")]},
            {"name": "Orders", "columns": [{"name": "Id"}], "partitions": [
                m('let Source = Stage, Y = Table.SelectRows(Source, each [Year] = 2023) in Y', name="2023"),
                m('let Source = Stage, Y = Table.SelectRows(Source, each [Year] = 2024) in Y', name="2024"),
                m('let Source = Stage, Y = Table.SelectRows(Source, each [Year] = 2024) in Y', name="2024 copy")]},
            {"name": "Dates", "columns": [{"name": "Date"}],
             "partitions": [{"name": "Dates", "source": {"type": "calculated", "expression": "CALENDARAUTO()"}}]}],
        "expressions": [
            {"name": "BaseUrl", "kind": "m", "queryGroup": "Parameters", "lineageTag": "aaaa0000-0000-4000-8000-000000000001",
             "expression": '"https://files.contoso.com/" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'},
            {"name": "Region", "kind": "m", "queryGroup": "Parameters",
             "expression": '"West" meta [IsParameterQuery=true, Type="Text"]'},
            {"name": "Stage", "kind": "m", "queryGroup": "Staging", "expression": STAGE, "description": "The staged file.",
             "annotations": [{"name": "PBI_ResultType", "value": "Table"}]},
            {"name": "fnClean", "kind": "m", "expression": "(t as table) as table =>\nlet\n    Trimmed = Table.TransformColumns(t, {})\nin\n    Trimmed",
             "annotations": [{"name": "PBI_ResultType", "value": "Function"}]},
            {"name": "Orphan", "kind": "m", "expression": 'let Source = #table({"A"}, {{1}}) in Source'},
            {"name": "Measure helper", "kind": "dax", "expression": "1"}]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def parse(self, doc):
        path = self.tmp / "model.bim"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return parse_model(path)

    def rows(self, doc=None):
        model = self.parse(doc or model_doc())
        return {row["queryName"]: row for row in build_source_queries(model, None)}, model


class Inventory(Base):
    def test_a_record_field_named_like_a_query_does_not_hide_the_query(self):
        doc = model_doc()
        doc["model"]["tables"][1]["partitions"] = [m('let\n    Source = Stage,\n    Added = Table.AddColumn(Source, "Details", '
                                                     'each [Stage = 1])\nin\n    Added', name="Customers")]
        rows, _ = self.rows(doc)
        self.assertEqual([i.split(":")[-1] for i in rows["Customers"]["upstream"]], ["Stage"])
        self.assertEqual(rows["Stage"]["usedBy"], ["Customers", "Orders"])

    def test_a_parameter_named_like_a_query_does_not_hide_it_after_the_function(self):
        doc = model_doc()
        doc["model"]["tables"][1]["partitions"] = [m('let\n    Transform = (Stage) => Stage\nin\n    Transform(Stage)', name="Customers")]
        rows, _ = self.rows(doc)
        self.assertEqual([i.split(":")[-1] for i in rows["Customers"]["upstream"]], ["Stage"])
        self.assertEqual(rows["Stage"]["usedBy"], ["Customers", "Orders"])

    def test_one_entry_per_query_with_what_it_is_and_what_it_feeds(self):
        rows, _ = self.rows()
        self.assertEqual(sorted(rows), ["BaseUrl", "Customers", "Orders / 2023", "Orders / 2024", "Orphan", "Region", "Sales",
                                        "Stage", "fnClean"])
        self.assertEqual({n: r["kind"] for n, r in rows.items() if r["kind"] != "query"},
                         {"BaseUrl": "parameter", "Region": "parameter", "fnClean": "function"})
        self.assertEqual({n: r["load"] for n, r in rows.items()},
                         {"BaseUrl": "not loaded", "Region": "not loaded", "Stage": "not loaded", "fnClean": "not loaded",
                          "Orphan": "not loaded", "Sales": "loaded", "Customers": "loaded", "Orders / 2023": "loaded",
                          "Orders / 2024": "loaded"})
        sales = rows["Sales"]
        self.assertEqual((sales["origin"], sales["table"], sales["partitions"]), ("table", "Sales", ["Sales-1f"]))
        self.assertEqual([i.split(":")[-1] for i in sales["upstream"]], ["Region", "fnClean"])
        self.assertEqual(sales["sources"], ["SQL Server · srv / dw · dbo.Orders"])
        self.assertEqual(sales["usedBy"], [])
        self.assertEqual((sales["extraction"]["status"], sales["steps"]["status"]), ("complete", "parsed"))

    def test_a_query_used_by_several_tables_is_listed_once_with_used_by(self):
        rows, _ = self.rows()
        stage = rows["Stage"]
        self.assertEqual(stage["usedBy"], ["Customers", "Orders"])
        self.assertEqual(sorted(i.split(":")[-1] for i in stage["referencedBy"]), ["Customers", "Orders / 2023", "Orders / 2024"])
        # through another query: BaseUrl is read by Stage, which the two tables read
        self.assertEqual(rows["BaseUrl"]["usedBy"], ["Customers", "Orders"])
        self.assertEqual(rows["Region"]["usedBy"], ["Sales"])
        self.assertEqual(rows["Orphan"]["usedBy"], [])
        self.assertEqual(rows["Orphan"]["sources"], ["Entered data"])
        self.assertEqual(rows["Stage"]["sources"], ["Web / API · https://files.contoso.com/stage.csv"])

    def test_partitions_with_the_same_text_are_one_query_and_different_text_one_each(self):
        rows, _ = self.rows()
        self.assertEqual(rows["Orders / 2023"]["partitions"], ["2023"])
        self.assertEqual(rows["Orders / 2024"]["partitions"], ["2024", "2024 copy"])
        doc = model_doc()
        doc["model"]["tables"][2]["partitions"] = [m("let S = Stage in S", name="a"), m("let S = Stage in S", name="b")]
        rows, _ = self.rows(doc)
        self.assertEqual(rows["Orders"]["partitions"], ["a", "b"])
        self.assertNotIn("Orders / a", rows)

    def test_the_same_query_supplied_twice_is_listed_once(self):
        doc = model_doc()
        doc["model"]["expressions"].append({"name": "Sales", "kind": "m", "expression": SALES + "\n"})
        rows, model = self.rows(doc)
        self.assertEqual(sum(1 for r in build_source_queries(model, None) if r["queryName"] == "Sales"), 1)
        self.assertEqual((rows["Sales"]["origin"], rows["Sales"]["alsoShared"]), ("table", True))

    def test_the_same_name_with_different_text_keeps_both(self):
        doc = model_doc()
        doc["model"]["expressions"].append({"name": "Sales", "kind": "m", "expression": 'let Source = Stage in Source'})
        model = self.parse(doc)
        both = [r for r in build_source_queries(model, None) if r["queryName"] == "Sales"]
        self.assertEqual(sorted(r["origin"] for r in both), ["shared", "table"])
        self.assertEqual(sorted(r["objectId"] for r in both), ["pbi:query:name:Sales", "pbi:query:name:Sales~2"])
        self.assertEqual(sorted(r["load"] for r in both), ["loaded", "not loaded"])

    @unittest.skipUnless(shutil.which("node"), "Node needed to run the generated script")
    def test_two_queries_of_one_name_and_a_step_returned_early_in_the_view(self):
        doc = model_doc()
        doc["model"]["expressions"] += [{"name": "Sales", "kind": "m", "expression": 'let Source = Stage in Source'},
                                        {"name": "Out of order", "kind": "m", "expression": "let Result = Final, Final = 42 in Result"}]
        model = self.parse(doc)
        html = render_html(build_payload(model, None, None, "Shop"), self.tmp / "same.html")
        result = subprocess.run(["node", str(Path(__file__).with_name("check_query_cases.cjs")), str(html)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ids_are_unique_follow_lineage_tags_and_match_the_object_index(self):
        model = self.parse(model_doc())
        payload = build_payload(model, None, None, "Shop")
        rows = payload["sourceQueries"]
        ids = [r["objectId"] for r in rows]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertIn("pbi:query:aaaa0000-0000-4000-8000-000000000001", ids)       # BaseUrl keeps its tag
        index = build_search_index(payload)
        indexed = {item[3]: item[1] for item in index["items"] if index["kinds"][item[0]] == "query"}
        self.assertEqual(indexed, {r["objectId"]: r["queryName"] for r in rows})
        for row in rows:
            for target in row["upstream"] + row["referencedBy"]:
                self.assertIn(target, ids)

    def test_folders_and_order_are_recreated_when_the_file_records_them(self):
        rows, model = self.rows()
        self.assertEqual([g["folder"] for g in query_groups(model)], ["Parameters", "Staging"])
        self.assertEqual(query_groups(model)[1]["description"], "Read once, used twice.")
        self.assertEqual({n: r["group"] for n, r in rows.items() if r["group"]},
                         {"BaseUrl": "Parameters", "Region": "Parameters", "Stage": "Staging"})
        ordered = [n for n, r in sorted(rows.items(), key=lambda item: item[1]["order"])]
        self.assertEqual(ordered[:6], ["BaseUrl", "Region", "Stage", "Sales", "Customers", "fnClean"])

    def test_without_folders_the_list_is_flat_in_file_order(self):
        doc = model_doc()
        del doc["model"]["queryGroups"], doc["model"]["annotations"]
        for e in doc["model"]["expressions"]:
            e.pop("queryGroup", None)
        rows, model = self.rows(doc)
        self.assertEqual(query_groups(model), [])
        self.assertTrue(all(not r["group"] for r in rows.values()))
        ordered = [n for n, r in sorted(rows.items(), key=lambda item: item[1]["order"])]
        self.assertEqual(ordered, ["Sales", "Customers", "Orders / 2023", "Orders / 2024", "BaseUrl", "Region", "Stage", "fnClean",
                                   "Orphan"])

    def test_the_three_csv_columns_are_unchanged_and_rows_hold_no_code_elsewhere(self):
        model = self.parse(model_doc())
        rows = build_source_queries(model, None)
        self.assertEqual([list(r)[:3] for r in rows], [["report", "queryName", "mCode"]] * len(rows))
        self.assertEqual([r["queryName"] for r in rows], sorted((r["queryName"] for r in rows), key=str.casefold))
        for row in rows:
            rest = json.dumps({k: v for k, v in row.items() if k != "mCode"})
            for fragment in ("Sql.Database", "Table.SelectRows", "Csv.Document", "=>", "meta ["):
                self.assertNotIn(fragment, rest, row["queryName"])

    def test_an_entity_partition_uses_the_expression_it_reads_through(self):
        doc = model_doc()
        doc["model"]["expressions"].append({"name": "DatabaseQuery", "kind": "m",
                                            "expression": 'let database = Sql.Database("lake.sql", "wh") in database'})
        doc["model"]["tables"].append({"name": "Lake", "columns": [{"name": "A"}], "partitions": [
            {"name": "Lake", "mode": "directLake", "source": {"type": "entity", "entityName": "lake_t", "schemaName": "dbo",
                                                              "expressionSource": "DatabaseQuery"}}]})
        rows, _ = self.rows(doc)
        self.assertEqual(rows["DatabaseQuery"]["usedBy"], ["Lake"])
        self.assertNotIn("Lake", rows)                      # an entity partition is not a Power Query query

    def test_tables_defined_without_power_query_have_no_entry(self):
        rows, _ = self.rows()
        self.assertNotIn("Dates", rows)
        doc = {"model": {"name": "AS", "dataSources": [{"name": "dw", "connectionString": "Provider=SQLNCLI11;Data Source=s;Initial Catalog=d"}],
                         "tables": [{"name": "T", "columns": [{"name": "A"}],
                                     "partitions": [{"name": "T", "source": {"query": "SELECT * FROM dbo.T", "dataSource": "dw"}}]}]}}
        rows, _ = self.rows(doc)
        self.assertEqual(rows, {})


class LegacyPackage(Base):
    """Pre-2019 files keep Power Query in a package; the model stores a placeholder per table."""

    SECTION = ('section Section1;\n\nshared Age = let\n    Source = Csv.Document(File.Contents("C:\\data\\age.csv"))\nin\n    Source;\n\n'
               'shared Folder = "C:\\data" meta [IsParameterQuery=true, Type="Text"];\n\n'
               'shared #"Not loaded" = let Source = #table({"A"}, {{1}}) in Source;\n')

    def doc(self, section=SECTION, location="Age"):
        source = {"name": "ds1", "connectionString": f"Provider=Microsoft.PowerBI.OleDb;Global Pipe=x;Mashup=\"\";Location={location}"}
        if section is not None:
            source["mashupSection"] = section
        return {"model": {"name": "Old", "dataSources": [source], "tables": [
            {"name": "Age", "columns": [{"name": "A"}],
             "partitions": [{"name": "Age-1", "source": {"type": "query", "query": "SELECT * FROM [Age]", "dataSource": "ds1"}}]}]}}

    def test_the_member_a_table_loads_is_its_query_and_the_rest_are_shared(self):
        rows, _ = self.rows(self.doc())
        self.assertEqual(sorted(rows), ["Age", "Folder", "Not loaded"])
        self.assertEqual((rows["Age"]["origin"], rows["Age"]["load"]), ("table", "loaded"))
        self.assertIn("Csv.Document", rows["Age"]["mCode"])
        self.assertNotIn("SELECT * FROM", json.dumps(list(rows.values())))     # the placeholder is never a query
        self.assertEqual((rows["Folder"]["kind"], rows["Folder"]["load"]), ("parameter", "not loaded"))
        self.assertEqual(rows["Not loaded"]["load"], "not loaded")

    def test_a_package_that_cannot_be_read_leaves_a_named_query_without_text(self):
        rows, _ = self.rows(self.doc(section=None))
        self.assertEqual(list(rows), ["Age"])
        age = rows["Age"]
        self.assertEqual((age["mCode"], age["extraction"]["status"], age["load"]), ("", "unavailable", "loaded"))
        self.assertIn("package that could not be read", age["extraction"]["note"])

    def test_load_status_is_unknown_when_a_table_could_not_be_matched_to_its_query(self):
        rows = build_source_queries(self.parse(self.doc(location="Renamed")), None)
        table = next(r for r in rows if r["origin"] == "table")
        self.assertEqual((table["queryName"], table["extraction"]["status"], table["load"]), ("Age", "unavailable", "loaded"))
        # Any other member may be the one that table loads, so none of them is called "not loaded".
        self.assertEqual({r["queryName"]: r["load"] for r in rows if r["origin"] == "shared"},
                         {"Age": "unknown", "Folder": "unknown", "Not loaded": "unknown"})
        self.assertEqual(len({r["objectId"] for r in rows}), 4)


class Readers(Base):
    """Folders, order, tags and descriptions arrive the same from every format that records them."""

    def check(self, model):
        rows = {r["queryName"]: r for r in build_source_queries(model, None)}
        self.assertEqual([(g["folder"], g["order"]) for g in query_groups(model)], [("Parameters", 0), ("Staging", 1)])
        self.assertEqual((rows["Stage"]["group"], rows["BaseUrl"]["group"], rows["Sales"]["group"]), ("Staging", "Parameters", "Loaded"))
        self.assertEqual(rows["BaseUrl"]["objectId"], "pbi:query:aaaa0000-0000-4000-8000-000000000001")
        self.assertEqual(rows["Stage"]["description"], "The staged file.")
        self.assertEqual([n for n, r in sorted(rows.items(), key=lambda i: i[1]["order"])][:3], ["BaseUrl", "Stage", "Sales"])
        self.assertEqual(rows["fnClean"]["kind"], "function")
        return rows

    def test_tmdl(self):
        definition = self.tmp / "Shop.SemanticModel" / "definition"
        (definition / "tables").mkdir(parents=True)
        (definition / "model.tmdl").write_text(
            "model Model\n\tculture: en-US\n\nqueryGroup Staging\n\n\tannotation PBI_QueryGroupOrder = 1\n\n"
            "/// Values the queries read\nqueryGroup Parameters\n\n\tannotation PBI_QueryGroupOrder = 0\n\n"
            'annotation PBI_QueryOrder = ["BaseUrl","Stage","Sales","fnClean"]\n\nref table Sales\n', encoding="utf-8")
        (definition / "expressions.tmdl").write_text(
            'expression BaseUrl = "https://files.contoso.com/" meta [IsParameterQuery=true, Type="Text"]\n'
            "\tlineageTag: aaaa0000-0000-4000-8000-000000000001\n\tqueryGroup: Parameters\n\n\tannotation PBI_ResultType = Text\n\n"
            "/// The staged file.\nexpression Stage =\n\t\tlet\n\t\t    Source = Csv.Document(Web.Contents(BaseUrl & \"stage.csv\"))\n"
            "\t\tin\n\t\t    Source\n\tlineageTag: bbbb0000-0000-4000-8000-000000000002\n\tqueryGroup: Staging\n\n"
            "\tannotation PBI_ResultType = Table\n\n"
            "expression fnClean =\n\t\t(t as table) as table =>\n\t\tlet\n\t\t    Trimmed = Table.TransformColumns(t, {})\n\t\tin\n"
            "\t\t    Trimmed\n\tlineageTag: cccc0000-0000-4000-8000-000000000003\n\n\tannotation PBI_ResultType = Function\n", encoding="utf-8")
        (definition / "tables" / "Sales.tmdl").write_text(
            "table Sales\n\tlineageTag: dddd0000-0000-4000-8000-000000000004\n\n\tcolumn Amount\n\t\tdataType: double\n\t\tsourceColumn: Amount\n\n"
            "\tpartition Sales-1f = m\n\t\tmode: import\n\t\tqueryGroup: Loaded\n\t\tsource =\n\t\t\t\tlet\n\t\t\t\t    Source = fnClean(Stage)\n"
            "\t\t\t\tin\n\t\t\t\t    Source\n\n\tannotation PBI_ResultType = Table\n", encoding="utf-8")
        rows = self.check(parse_model(definition.parent))
        self.assertEqual(rows["Stage"]["objectId"], "pbi:query:bbbb0000-0000-4000-8000-000000000002")
        self.assertEqual(rows["Stage"]["usedBy"], ["Sales"])
        self.assertEqual(rows["Sales"]["mCode"], "let\n    Source = fnClean(Stage)\nin\n    Source")
        self.assertEqual(query_groups(parse_model(definition.parent))[0]["description"], "Values the queries read")

    def tmsl(self):
        return {"model": {
            "name": "Shop", "annotations": [{"name": "PBI_QueryOrder", "value": '["BaseUrl","Stage","Sales","fnClean"]'}],
            "queryGroups": [{"folder": "Staging", "annotations": [{"name": "PBI_QueryGroupOrder", "value": "1"}]},
                            {"folder": "Parameters", "annotations": [{"name": "PBI_QueryGroupOrder", "value": "0"}]}],
            "tables": [{"name": "Sales", "columns": [{"name": "Amount"}], "partitions": [
                {"name": "Sales-1f", "queryGroup": "Loaded", "source": {"type": "m", "expression": "let\n    Source = fnClean(Stage)\nin\n    Source"}}]}],
            "expressions": [
                {"name": "BaseUrl", "kind": "m", "queryGroup": "Parameters", "lineageTag": "aaaa0000-0000-4000-8000-000000000001",
                 "expression": '"https://files.contoso.com/" meta [IsParameterQuery=true, Type="Text"]'},
                {"name": "Stage", "kind": "m", "queryGroup": "Staging", "description": "The staged file.", "expression": STAGE},
                {"name": "fnClean", "kind": "m", "expression": "(t as table) as table =>\nlet\n    Trimmed = Table.TransformColumns(t, {})\nin\n    Trimmed"}]}}

    def test_bim(self):
        self.check(self.parse(self.tmsl()))

    def test_pbi_tools_extract_folder(self):
        doc = self.tmsl()
        root = self.tmp / "Model"
        (root / "queries").mkdir(parents=True)
        table = doc["model"].pop("tables")[0]
        (root / "queries" / "Sales.m").write_text(table["partitions"][0]["source"].pop("expression"), encoding="utf-8")
        for expression in doc["model"]["expressions"]:
            (root / "queries" / f"{expression['name']}.m").write_text(expression.pop("expression"), encoding="utf-8")
        (root / "tables" / "Sales").mkdir(parents=True)
        (root / "tables" / "Sales" / "table.json").write_text(json.dumps(table), encoding="utf-8")
        (root / "database.json").write_text(json.dumps(doc), encoding="utf-8")
        rows = self.check(self.parse(assemble(root)))
        self.assertEqual(rows["Sales"]["extraction"]["status"], "complete")
        (root / "queries" / "Stage.m").unlink()          # the extract names the query but its file is gone
        rows = {r["queryName"]: r for r in build_source_queries(self.parse(assemble(root)), None)}
        self.assertEqual((rows["Stage"]["extraction"]["status"], rows["Stage"]["steps"]["status"]), ("unavailable", "none"))

    def test_pbix_and_abf_metadata(self):
        schema = '''
CREATE TABLE Model (ID, Name, DefaultMode, Culture);
CREATE TABLE "Table" (ID, Name, Description, IsHidden, DataCategory, LineageTag, SystemFlags, CalculationGroupID);
CREATE TABLE Column (ID, TableID, Type, SortByColumnID, ExplicitName, InferredName, ExplicitDataType, InferredDataType);
CREATE TABLE Partition (ID, TableID, Name, Type, Mode, QueryDefinition, DataSourceID, QueryGroupID);
CREATE TABLE Measure (ID, TableID, Name, Expression, FormatString, IsHidden);
CREATE TABLE Annotation (ObjectID, Name, Value);
CREATE TABLE Expression (ID, ModelID, Name, Description, Kind, Expression, QueryGroupID, LineageTag);
CREATE TABLE QueryGroup (ID, ModelID, Folder, Description);
'''
        db = sqlite3.connect(":memory:")
        db.executescript(schema)
        fn = "(t as table) as table =>\nlet\n    Trimmed = Table.TransformColumns(t, {})\nin\n    Trimmed"
        for statement, args in (
                ("INSERT INTO Model VALUES (1, 'Shop', 0, 'en-US')", ()),
                ('INSERT INTO "Table" VALUES (10, \'Sales\', NULL, 0, NULL, NULL, 0, NULL)', ()),
                ("INSERT INTO Column VALUES (11, 10, 1, NULL, 'Amount', NULL, 8, 8)", ()),
                ("INSERT INTO QueryGroup VALUES (20, 1, 'Staging', NULL)", ()),
                ("INSERT INTO QueryGroup VALUES (21, 1, 'Parameters', NULL)", ()),
                ("INSERT INTO QueryGroup VALUES (22, 1, 'Loaded', NULL)", ()),
                ("INSERT INTO Partition VALUES (30, 10, 'Sales-1f', 4, 0, ?, NULL, 22)", ("let\n    Source = fnClean(Stage)\nin\n    Source",)),
                ("INSERT INTO Expression VALUES (40, 1, 'BaseUrl', NULL, 0, ?, 21, 'aaaa0000-0000-4000-8000-000000000001')",
                 ('"https://files.contoso.com/" meta [IsParameterQuery=true, Type="Text"]',)),
                ("INSERT INTO Expression VALUES (41, 1, 'Stage', 'The staged file.', 0, ?, 20, NULL)", (STAGE,)),
                ("INSERT INTO Expression VALUES (42, 1, 'fnClean', NULL, 0, ?, NULL, NULL)", (fn,)),
                ("INSERT INTO Annotation VALUES (1, 'PBI_QueryOrder', ?)", ('["BaseUrl","Stage","Sales","fnClean"]',)),
                ("INSERT INTO Annotation VALUES (20, 'PBI_QueryGroupOrder', '1')", ()),
                ("INSERT INTO Annotation VALUES (21, 'PBI_QueryGroupOrder', '0')", ()),
                ("INSERT INTO Annotation VALUES (22, 'PBI_QueryGroupOrder', '2')", ()),
                ("INSERT INTO Annotation VALUES (42, 'PBI_ResultType', 'Function')", ())):
            db.execute(statement, args)
        db.commit()
        raw = db.serialize()
        db.close()
        with mock.patch.object(portable, "_metadata", side_effect=lambda *a, **k: input_limits.open_metadata(raw)):
            document = portable.model_document("model.pbix")
        path = self.tmp / "model.bim"
        path.write_text(json.dumps(document), encoding="utf-8")
        model = parse_model(path)
        self.assertEqual([g["folder"] for g in query_groups(model)], ["Parameters", "Staging", "Loaded"])
        model["queryGroups"] = [g for g in model["queryGroups"] if g["folder"] != "Loaded"]
        self.check(model)

    def test_older_metadata_without_folders_is_a_flat_list_not_an_error(self):
        from test_portable_reader import MODEL, column, document, table
        model = document(MODEL, table(1, "A"), column(10, 1, "a1"),
                         "INSERT INTO Partition VALUES (1, 1, 'pA', 4, 0, 'let x = 1 in x', NULL)")
        self.assertNotIn("queryGroups", model)
        self.assertNotIn("queryGroup", model["tables"][0]["partitions"][0])


class View(Base):
    """What the page is given: stored facts plus what only the rendered copy can say."""

    def payload(self, doc=None):
        return build_payload(self.parse(doc or model_doc()), None, None, "Shop")

    def withheld(self, payload):
        """What the platform's shared projection does to this payload with code withheld: every string holding
        query code becomes the marker. (The projection itself is tested in packages/engines.)"""
        code = ("Sql.Database", "Table.", "Csv.Document", "meta [", "=>", "#table", "let ")

        def visit(node):
            if isinstance(node, dict):
                return {key: visit(value) for key, value in node.items()}
            if isinstance(node, list):
                return [visit(value) for value in node]
            if isinstance(node, str) and any(fragment in node for fragment in code):
                return CODE_WITHHELD
            return node
        copy = visit(json.loads(json.dumps(payload)))
        for row in copy["sourceQueries"]:
            row["mCode"] = CODE_WITHHELD
        for expression in copy["model"]["expressions"]:
            expression["expression"] = CODE_WITHHELD
        return copy

    def test_local_copy_has_steps_and_says_included(self):
        view = power_query_view(self.payload())
        self.assertTrue(view["recorded"])
        sales = next(q for q in view["queries"] if q["name"] == "Sales")
        self.assertEqual(sales["publication"], "included")
        self.assertEqual([s["name"] for s in sales["steps"]["items"]], ["Source", "Orders", "Kept Rows", "Cleaned"])
        self.assertEqual((sales["extraction"]["status"], sales["steps"]["status"]), ("complete", "parsed"))
        self.assertNotIn("mCode", sales)                       # the script is on the page once, in the payload
        self.assertEqual([g["folder"] for g in view["groups"]], ["Parameters", "Staging"])

    def test_withheld_copy_keeps_the_facts_and_drops_everything_read_from_the_script(self):
        local = self.payload()
        view = power_query_view(self.withheld(local))
        sales = next(q for q in view["queries"] if q["name"] == "Sales")
        self.assertEqual(sales["publication"], "withheld")
        self.assertEqual((sales["extraction"]["status"], sales["steps"]["status"]), ("complete", "parsed"))   # read at generation
        self.assertNotIn("items", sales["steps"])
        text = json.dumps(view)
        for fragment in ("Kept Rows", "Sql.Database", "Table.SelectRows", "Trimmed"):
            self.assertNotIn(fragment, text)
        self.assertEqual(sales["table"], "Sales")
        self.assertEqual(next(q for q in view["queries"] if q["name"] == "Stage")["usedBy"], ["Customers", "Orders"])

    def test_cleaned_copy_says_cleaned_and_reads_steps_from_the_cleaned_text(self):
        payload = self.payload()
        row = next(r for r in payload["sourceQueries"] if r["queryName"] == "Sales")
        row["mCode"] = row["mCode"].replace('"srv"', '"srv", [Password="[credential withheld]"]')
        sales = next(q for q in power_query_view(payload)["queries"] if q["name"] == "Sales")
        self.assertEqual((sales["publication"], len(sales["steps"]["items"])), ("cleaned", 4))
        self.assertEqual(publication_of("let A = 1 in A"), "included")

    def test_the_three_statuses_vary_independently(self):
        doc = model_doc()
        doc["model"]["expressions"] += [
            {"name": "Cut", "kind": "m", "expression": 'let Source = Sql.Database("s", "d'},
            {"name": "Odd", "kind": "m", "expression": "let A = 1, A = 2 in A"},
            {"name": "Empty", "kind": "m", "expression": ""}]
        payload = self.payload(doc)
        seen = set()
        for copy in (payload, self.withheld(payload)):
            for q in power_query_view(copy)["queries"]:
                seen.add((q["extraction"]["status"], q["steps"]["status"], q["publication"]))
        for combination in (("complete", "parsed", "included"), ("complete", "parsed", "withheld"), ("complete", "none", "included"),
                            ("complete", "unsupported", "included"), ("known partial", "unsupported", "included"),
                            ("known partial", "unsupported", "withheld"), ("unavailable", "none", "included")):
            self.assertIn(combination, seen)

    def test_a_payload_from_before_the_facts_were_recorded_is_read_again_from_its_model(self):
        payload = self.payload()
        old = dict(payload, sourceQueries=[{k: r[k] for k in ("report", "queryName", "mCode")} for r in payload["sourceQueries"]])
        view = power_query_view(old)
        self.assertTrue(view["recorded"])
        stage = next(q for q in view["queries"] if q["name"] == "Stage")
        self.assertEqual((stage["usedBy"], stage["load"], stage["extraction"]["status"]), (["Customers", "Orders"], "not loaded", "complete"))
        index = build_search_index(old)
        self.assertEqual(sorted(q["objectId"] for q in view["queries"]),
                         sorted(item[3] for item in index["items"] if index["kinds"][item[0]] == "query"))
        self.assertEqual(next(q for q in view["queries"] if q["name"] == "BaseUrl")["objectId"], "pbi:query:name:BaseUrl")

    def test_an_old_shared_copy_says_not_recorded_rather_than_guessing(self):
        payload = self.payload()
        old = self.withheld(dict(payload, sourceQueries=[{k: r[k] for k in ("report", "queryName", "mCode")}
                                                         for r in payload["sourceQueries"]]))
        view = power_query_view(old)
        self.assertFalse(view["recorded"])
        for q in view["queries"]:
            self.assertEqual((q["extraction"]["status"], q["steps"]["status"], q["publication"]),
                             ("not recorded", "not recorded", "withheld"))
            self.assertNotIn("load", q)
        index = build_search_index(old)
        self.assertEqual(sorted(q["objectId"] for q in view["queries"]),
                         sorted(item[3] for item in index["items"] if index["kinds"][item[0]] == "query"))

    @unittest.skipUnless(shutil.which("node"), "Node needed to run the generated script")
    def test_the_rendered_view(self):
        payload = self.payload()
        script = Path(__file__).with_name("check_power_query.cjs")
        for name, copy in (("local", payload), ("withheld", self.withheld(payload))):
            with self.subTest(copy=name):
                html = render_html(copy, self.tmp / f"{name}.html")
                result = subprocess.run(["node", str(script), str(html), name], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if name == "withheld":
                    text = html.read_text(encoding="utf-8")
                    for fragment in ("Kept Rows", "Sql.Database(", "Table.SelectRows"):
                        self.assertNotIn(fragment, text)


if __name__ == "__main__":
    unittest.main()
