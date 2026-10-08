"""How a table is defined and what kind each column is: one answer each, from the object's own metadata."""
import json
import shutil
import sqlite3
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
from pbidocgen.renderer import build_derived, build_payload, render_html  # noqa: E402
from pbidocgen.table_kinds import KINDS, column_type, defined_by, is_automatic_date_table  # noqa: E402


def part(kind, expression="", **more):
    source = {"type": kind}
    if kind == "query":
        source = {"query": expression, "dataSource": "dw"}
    elif kind == "entity":
        source.update(entityName="t", expressionSource="DatabaseQuery")
    elif expression:
        source["expression"] = expression
    return {"name": more.pop("name", "p"), "source": source, **more}


def doc():
    marker = lambda name: [{"name": name, "value": "true"}]   # noqa: E731
    return {"model": {"name": "Kinds", "dataSources": [{"name": "dw", "connectionString": "Provider=SQLNCLI11;Data Source=s;Initial Catalog=d"}],
                      "expressions": [{"name": "DatabaseQuery", "kind": "m", "expression": 'let d = Sql.Database("s", "d") in d'}],
                      "relationships": [{"name": "r", "fromTable": "Sales", "fromColumn": "DateKey", "toTable": "Calendar", "toColumn": "Date"}],
                      "tables": [
        {"name": "Sales", "partitions": [part("m", 'let S = Sql.Database("s", "d") in S')],
         "columns": [{"name": "Amount", "sourceColumn": "Amount"}, {"name": "DateKey"},
                     {"name": "Double", "type": "calculated", "expression": "[Amount] * 2"}],
         "measures": [{"name": "Total", "expression": "SUM(Sales[Amount])"}]},
        {"name": "Budget", "partitions": [part("query", "SELECT * FROM dbo.Budget")], "columns": [{"name": "Amount"}]},
        {"name": "Lake", "partitions": [part("entity", mode="directLake")], "columns": [{"name": "Qty"}]},
        {"name": "Calendar", "dataCategory": "Time", "partitions": [part("calculated", "CALENDAR(DATE(2024,1,1), DATE(2024,12,31))")],
         "columns": [{"name": "Date", "type": "calculatedTableColumn", "sourceColumn": "[Date]"},
                     {"name": "Year", "type": "calculated", "expression": "YEAR([Date])"}]},
        {"name": "LocalDateTable_7f", "partitions": [part("calculated", "Calendar(Date(2020,1,1), Date(2020,12,31))")],
         "annotations": marker("__PBI_LocalDateTable"), "columns": [{"name": "Date", "type": "calculatedTableColumn"}]},
        {"name": "DateTableTemplate_9a", "partitions": [part("calculated", "Calendar(Date(2015,1,1), Date(2015,1,1))")],
         "annotations": marker("__PBI_TemplateDateTable"), "columns": [{"name": "Date", "type": "calculatedTableColumn"}]},
        {"name": "LocalDateTable_lookalike", "partitions": [part("calculated", "{1, 2}")], "columns": [{"name": "Value", "type": "calculatedTableColumn"}]},
        {"name": "Time Intelligence", "partitions": [part("calculationGroup")], "columns": [{"name": "Name"}],
         "calculationGroup": {"precedence": 5, "calculationItems": [{"name": "YTD", "expression": "CALCULATE(SELECTEDMEASURE())", "ordinal": 0}]}},
        {"name": "Mixed", "partitions": [part("m", "let a = 1 in a", name="a"), part("calculated", "{1}", name="b")], "columns": [{"name": "A"}]},
        {"name": "Refreshed", "partitions": [part("policyRange")], "columns": [{"name": "A"}]},
        {"name": "Empty", "partitions": [], "columns": [{"name": "A"}]}]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def model(self, document=None):
        path = self.tmp / "model.bim"
        path.write_text(json.dumps(document or doc()), encoding="utf-8")
        return parse_model(path)


class DefinedBy(Base):
    def test_every_table_gets_exactly_one_kind(self):
        tables = {t["name"]: t for t in self.model()["tables"]}
        self.assertEqual({name: t["definedBy"] for name, t in tables.items()}, {
            "Sales": {"kind": "Power Query"}, "Budget": {"kind": "SQL query"}, "Lake": {"kind": "Entity"},
            "Calendar": {"kind": "Calculated table"}, "LocalDateTable_7f": {"kind": "Automatic date table"},
            "DateTableTemplate_9a": {"kind": "Automatic date table"}, "LocalDateTable_lookalike": {"kind": "Calculated table"},
            "Time Intelligence": {"kind": "Calculation group"},
            "Mixed": {"kind": "Other", "raw": "m, calculated"}, "Refreshed": {"kind": "Other", "raw": "policyRange"},
            "Empty": {"kind": "Other", "raw": "no partition"}})
        self.assertTrue(all(t["definedBy"]["kind"] in KINDS for t in tables.values()))
        self.assertEqual(sorted({t["definedBy"]["kind"] for t in tables.values()}), sorted(KINDS))    # every kind is covered

    def test_an_automatic_date_table_is_one_only_when_the_file_marks_it(self):
        for name in ("LocalDateTable_x", "DateTableTemplate_x", "Calendar"):
            plain = {"name": name, "partitions": [{"type": "calculated"}], "annotations": {}}
            self.assertFalse(is_automatic_date_table(plain), name)
            self.assertEqual(defined_by(plain)["kind"], "Calculated table", name)
        for marker in ("__PBI_LocalDateTable", "__PBI_TemplateDateTable"):
            self.assertEqual(defined_by({"name": "Anything", "partitions": [{"type": "calculated"}], "annotations": {marker: "true"}})["kind"],
                             "Automatic date table")
            self.assertEqual(defined_by({"name": "LocalDateTable_x", "partitions": [{"type": "calculated"}], "annotations": {marker: "false"}})["kind"],
                             "Calculated table")
        # the file's own list shape is read too
        self.assertTrue(is_automatic_date_table({"annotations": [{"name": "__PBI_LocalDateTable", "value": "true"}]}))

    def test_the_role_label_is_unchanged(self):
        """`tableType` is the table's role in the model; adding "defined by" changed none of them."""
        roles = {t["name"]: t["tableType"] for t in self.model()["tables"]}
        self.assertEqual(roles, {"Sales": "fact", "Budget": "disconnected", "Lake": "disconnected", "Calendar": "date dimension",
                                 "LocalDateTable_7f": "date dimension", "DateTableTemplate_9a": "date dimension",
                                 "LocalDateTable_lookalike": "disconnected", "Time Intelligence": "calculation group",
                                 "Mixed": "disconnected", "Refreshed": "disconnected", "Empty": "disconnected"})

    def test_a_pre_2019_placeholder_is_power_query(self):
        document = {"model": {"name": "Old", "dataSources": [{"name": "ds", "connectionString":
                    "Provider=Microsoft.PowerBI.OleDb;Mashup=;Location=Age"}], "tables": [{"name": "Age", "columns": [{"name": "A"}],
                    "partitions": [{"name": "Age-1", "source": {"type": "query", "query": "SELECT * FROM [Age]", "dataSource": "ds"}}]}]}}
        self.assertEqual(self.model(document)["tables"][0]["definedBy"], {"kind": "Power Query"})

    def test_a_document_written_before_the_kind_was_recorded_gets_it_when_rendered(self):
        payload = build_payload(self.model(), None, None, "Kinds")
        self.assertEqual(build_derived(payload)["tableKinds"], {})                 # recorded: nothing to add
        recorded = {t["name"]: t["definedBy"] for t in payload["model"]["tables"]}
        for table in payload["model"]["tables"]:
            del table["definedBy"]
            for column in table["columns"]:
                column.pop("columnType", None)
        self.assertEqual(build_derived(payload)["tableKinds"], recorded)
        index = build_search_index(payload)
        kinds = {item[1]: index["kinds"][item[0]] for item in index["items"] if item[2] == -1}
        self.assertEqual((kinds["Calendar"], kinds["LocalDateTable_7f"], kinds["Time Intelligence"], kinds["Sales"]),
                         ("calculated table", "automatic date table", "calculation group", "table"))


class Columns(Base):
    def test_columns_are_classified_one_by_one(self):
        tables = {t["name"]: {c["name"]: (c["columnType"], c["isCalculated"]) for c in t["columns"]} for t in self.model()["tables"]}
        # an imported table with a calculated column; a calculated table with a column of each kind
        self.assertEqual(tables["Sales"], {"Amount": ("data", False), "DateKey": ("data", False), "Double": ("calculated", True)})
        self.assertEqual(tables["Calendar"], {"Date": ("calculatedTableColumn", False), "Year": ("calculated", True)})
        self.assertEqual(column_type({"type": "rowNumber"}), "rowNumber")
        self.assertEqual(column_type({"isCalculated": True}), "calculated")       # an older payload
        self.assertEqual(column_type({}), "data")

    def test_the_search_index_tags_tables_and_columns_with_their_kind(self):
        index = build_search_index(build_payload(self.model(), None, None, "Kinds"))
        tagged = {(item[1], index["parents"][item[2]] if item[2] >= 0 else ""): index["kinds"][item[0]] for item in index["items"]}
        self.assertEqual(tagged[("Double", "Sales")], "calculated column")
        self.assertEqual(tagged[("Year", "Calendar")], "calculated column")
        self.assertEqual(tagged[("Date", "Calendar")], "column")
        self.assertEqual(tagged[("Amount", "Sales")], "column")
        self.assertEqual({name: kind for (name, parent), kind in tagged.items() if not parent and kind != "query"}, {
            "Sales": "table", "Budget": "table", "Lake": "table", "Mixed": "table", "Refreshed": "table", "Empty": "table",
            "Calendar": "calculated table", "LocalDateTable_lookalike": "calculated table",
            "LocalDateTable_7f": "automatic date table", "DateTableTemplate_9a": "automatic date table",
            "Time Intelligence": "calculation group"})

    def test_tmdl_columns(self):
        definition = self.tmp / "T.SemanticModel" / "definition"
        (definition / "tables").mkdir(parents=True)
        (definition / "model.tmdl").write_text("model Model\n", encoding="utf-8")
        (definition / "tables" / "Calendar.tmdl").write_text(
            "table Calendar\n\n\tcolumn Date\n\t\tdataType: dateTime\n\t\tisNameInferred\n\t\tsourceColumn: [Date]\n\n"
            "\tcolumn Year = YEAR([Date])\n\t\tdataType: int64\n\n\tpartition Calendar = calculated\n\t\tmode: import\n"
            "\t\tsource = CALENDAR(DATE(2024, 1, 1), DATE(2024, 12, 31))\n", encoding="utf-8")
        (definition / "tables" / "LocalDateTable_1.tmdl").write_text(
            "table LocalDateTable_1\n\tisHidden\n\n\tcolumn Date\n\t\tdataType: dateTime\n\t\tisDataTypeInferred\n\t\tsourceColumn: [Date]\n\n"
            "\tpartition LocalDateTable_1 = calculated\n\t\tmode: import\n\t\tsource = Calendar(Date(2020, 1, 1), Date(2020, 12, 31))\n\n"
            "\tannotation __PBI_LocalDateTable = true\n", encoding="utf-8")
        (definition / "tables" / "Sales.tmdl").write_text(
            "table Sales\n\n\tcolumn Amount\n\t\tdataType: double\n\t\tsourceColumn: Amount\n\n\tcolumn Double = [Amount] * 2\n\n"
            "\tpartition Sales = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet S = 1 in S\n", encoding="utf-8")
        tables = {t["name"]: t for t in parse_model(definition.parent)["tables"]}
        self.assertEqual({c["name"]: c["columnType"] for c in tables["Calendar"]["columns"]}, {"Date": "calculatedTableColumn", "Year": "calculated"})
        self.assertEqual({c["name"]: c["columnType"] for c in tables["Sales"]["columns"]}, {"Amount": "data", "Double": "calculated"})
        self.assertEqual((tables["Calendar"]["definedBy"]["kind"], tables["LocalDateTable_1"]["definedBy"]["kind"], tables["Sales"]["definedBy"]["kind"]),
                         ("Calculated table", "Automatic date table", "Power Query"))

    def test_pbix_and_abf_columns(self):
        db = sqlite3.connect(":memory:")
        db.executescript('''
CREATE TABLE Model (ID, Name, DefaultMode, Culture);
CREATE TABLE "Table" (ID, Name, Description, IsHidden, DataCategory, LineageTag, SystemFlags, CalculationGroupID);
CREATE TABLE Column (ID, TableID, Type, SortByColumnID, ExplicitName, InferredName, ExplicitDataType, InferredDataType, Expression, SourceColumn);
CREATE TABLE Partition (ID, TableID, Name, Type, Mode, QueryDefinition, DataSourceID);
CREATE TABLE Measure (ID, TableID, Name, Expression, FormatString, IsHidden);
CREATE TABLE Annotation (ObjectID, Name, Value);
''')
        for statement in (
                "INSERT INTO Model VALUES (1, 'M', 0, 'en-US')",
                'INSERT INTO "Table" VALUES (10, \'Calendar\', NULL, 0, NULL, NULL, 0, NULL)',
                'INSERT INTO "Table" VALUES (20, \'LocalDateTable_x\', NULL, 1, NULL, NULL, 2, NULL)',
                "INSERT INTO Column VALUES (11, 10, 3, NULL, 'RowNumber-1', NULL, 6, 6, NULL, NULL)",
                "INSERT INTO Column VALUES (12, 10, 4, NULL, NULL, 'Date', 1, 9, NULL, '[Date]')",
                "INSERT INTO Column VALUES (13, 10, 2, NULL, 'Year', NULL, 6, 6, 'YEAR([Date])', NULL)",
                "INSERT INTO Column VALUES (21, 20, 4, NULL, NULL, 'Date', 1, 9, NULL, '[Date]')",
                "INSERT INTO Partition VALUES (30, 10, 'Calendar', 2, 0, 'CALENDAR(DATE(2024,1,1), DATE(2024,12,31))', NULL)",
                "INSERT INTO Partition VALUES (31, 20, 'LocalDateTable_x', 2, 0, 'Calendar(Date(2020,1,1), Date(2020,12,31))', NULL)",
                "INSERT INTO Annotation VALUES (20, '__PBI_LocalDateTable', 'true')"):
            db.execute(statement)
        db.commit()
        raw = db.serialize()
        db.close()
        with mock.patch.object(portable, "_metadata", side_effect=lambda *a, **k: input_limits.open_metadata(raw)):
            document = portable.model_document("model.abf")
        tables = {t["name"]: t for t in self.model(document)["tables"]}
        self.assertEqual({c["name"]: c["columnType"] for c in tables["Calendar"]["columns"]}, {"Date": "calculatedTableColumn", "Year": "calculated"})
        self.assertEqual((tables["Calendar"]["definedBy"]["kind"], tables["LocalDateTable_x"]["definedBy"]["kind"]),
                         ("Calculated table", "Automatic date table"))

    def test_calculation_items_keep_their_order_and_format_expression(self):
        document = doc()
        document["model"]["tables"][7]["calculationGroup"]["calculationItems"] = [
            {"name": "PY", "expression": "x", "ordinal": 1},
            {"name": "YTD", "expression": "y", "ordinal": 0, "formatStringDefinition": {"expression": '"#,0"'}}]
        group = next(t for t in self.model(document)["tables"] if t["name"] == "Time Intelligence")
        self.assertEqual(group["calculationGroupPrecedence"], 5)
        self.assertEqual([(i["name"], i.get("ordinal"), i.get("formatStringExpression")) for i in group["calculationGroup"]],
                         [("PY", 1, None), ("YTD", 0, '"#,0"')])


class Rendered(Base):
    @unittest.skipUnless(shutil.which("node"), "Node needed to run the generated script")
    def test_the_rendered_tabs_and_markers(self):
        import subprocess
        payload = build_payload(self.model(), None, None, "Kinds")
        script = Path(__file__).with_name("check_model_kinds.cjs")
        html = render_html(payload, self.tmp / "kinds.html")
        result = subprocess.run(["node", str(script), str(html)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # the same page from a payload written before these facts were recorded
        for table in payload["model"]["tables"]:
            del table["definedBy"]
        html = render_html(payload, self.tmp / "older.html")
        result = subprocess.run(["node", str(script), str(html)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
