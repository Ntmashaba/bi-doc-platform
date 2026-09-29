"""Partition sources come from the M tracer (parser merge).

Patterns are written from shapes seen in public PBIP projects: shared
parameters, folder file picks, web CSVs through shared queries, dataflows,
entered data and generated calendars. Every M partition must get the same
source on Overview/Lineage/Tables (model_parser) as on Sources (the tracer).
"""
import json
import tempfile
import unittest
from pathlib import Path

from pbidocgen.model_parser import parse_model
from pbidocgen.source_objects import build_source_objects

EXPRESSIONS = [
    {"name": "Server", "kind": "m", "expression": '"warehouse.example.net" meta [IsParameterQuery=true, Type="Text"]'},
    {"name": "Database", "kind": "m", "expression": '"dw" meta [IsParameterQuery=true, Type="Text"]'},
    {"name": "Schema", "kind": "m", "expression": '"sales" meta [IsParameterQuery=true, Type="Text"]'},
    {"name": "RawSales", "kind": "m", "expression":
        'let Source = Csv.Document(Web.Contents("https://example.org/data/RAW-Sales.csv"),[Delimiter=","]) in Source'},
    {"name": "Flow", "kind": "m", "expression":
        'let Source = PowerBI.Dataflows(), W = Source{[workspaceId="ws-1"]}[Data], '
        'D = W{[dataflowId="df-1"]}[Data], E = D{[entity="sales_fact"]}[Data] in E'},
]


def m(expression):
    return {"source": {"type": "m", "expression": expression}}


TABLES = {
    "Customer": m('let S = Sql.Databases(Server), DB = S{[Name=Database]}[Data], '
                  'T = DB{[Schema=Schema,Item="Customer"]}[Data] in T'),
    "Area": m('let S = Folder.Files("C:\\\\data"), F = S{[#"Folder Path"="C:\\\\data\\\\",Name="DimArea.csv"]}[Content], '
              'C = Csv.Document(F,[Delimiter=","]) in C'),
    "Sales": m('let S = RawSales, T = Table.PromoteHeaders(S) in T'),
    "Fact": m('let S = Flow in S'),
    "About": m('let S = #table({"Key","Value"},{{"Version","1.0"}}) in S'),
    "Targets": m('let S = Table.FromRecords({[Region="EU", Target=1]}) in S'),
    "Calendar": m('let S = List.Dates(#date(2024,1,1), 365, #duration(1,0,0,0)), '
                  'T = Table.FromList(S, Splitter.SplitByNothing()) in T'),
    "SalesCalendar": m('let Start = List.Min(RawSales[Order Date]), '
                       'S = List.Dates(Start, 10, #duration(1,0,0,0)), T = Table.FromList(S, Splitter.SplitByNothing()) in T'),
    "Snow": m('let S = Snowflake.Databases("acct.snowflakecomputing.com","WH"), D = S{[Name="SALES",Kind="Database"]}[Data], '
              'Sc = D{[Name="PUBLIC",Kind="Schema"]}[Data], T = Sc{[Name="ORDERS",Kind="Table"]}[Data] in T'),
    "Vault": m('let S = DataVault.Contents("https://vault.example.com"), T = S{[Schema="mart",Item="orders"]}[Data] in T'),
    "Calc": {"source": {"type": "calculated", "expression": "CALENDARAUTO()"}},
}


def build_model():
    raw = {"model": {"name": "Patterns", "expressions": EXPRESSIONS, "tables": [
        {"name": name, "columns": [{"name": "Id"}], "partitions": [dict(part, name=name)]}
        for name, part in TABLES.items()]}}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model.bim"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return parse_model(path)


class PartitionSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = build_model()
        cls.src = {t["name"]: t["partitions"][0]["source"] for t in cls.model["tables"]}

    def check(self, table, source_type, label, status=None):
        s = self.src[table]
        self.assertEqual(s["sourceType"], source_type, table)
        self.assertEqual(s["label"], label, table)
        if status:
            self.assertEqual(s["traceStatus"], status, table)

    def test_parameters_and_shared_queries_are_followed(self):
        self.check("Customer", "SQL Server", "SQL Server \u00b7 warehouse.example.net / dw", "Resolved")
        self.assertEqual((self.src["Customer"]["schema"], self.src["Customer"]["object"]), ("sales", "Customer"))
        self.check("Sales", "Web / API", "Web / API \u00b7 https://example.org/data/RAW-Sales.csv")
        self.check("Fact", "Power BI dataflow", "Power BI dataflow \u00b7 sales_fact", "Resolved")

    def test_file_picked_from_a_folder_is_that_file(self):
        self.check("Area", "CSV file", "CSV file \u00b7 DimArea.csv", "Resolved")

    def test_tables_built_inside_power_query(self):
        self.check("About", "Entered data", "Entered data", "Not applicable")
        self.check("Targets", "Entered data", "Entered data", "Not applicable")
        self.check("Calendar", "Generated in Power Query", "Generated in Power Query", "Not applicable")
        # A calendar built from another query's dates depends on that query.
        self.assertEqual(self.src["SalesCalendar"]["sourceType"], "Web / API")

    def test_connectors_known_to_both_paths(self):
        self.check("Snow", "Snowflake", "Snowflake \u00b7 acct.snowflakecomputing.com / SALES", "Resolved")
        self.assertEqual((self.src["Snow"]["schema"], self.src["Snow"]["object"]), ("PUBLIC", "ORDERS"))
        self.assertEqual(self.src["Vault"]["sourceType"], "DataVault.Contents (unrecognised connector)")
        self.assertEqual(self.src["Calc"]["sourceType"], "Calculated (DAX)")

    def test_overview_and_sources_agree_on_every_partition(self):
        rows = build_source_objects(self.model, None, None)
        for table, source in self.src.items():
            traced = {r["sourceType"] for r in rows if r["table"] == table}
            self.assertIn(source["sourceType"], traced, table)

    def test_nothing_is_left_unknown(self):
        self.assertFalse([t for t, s in self.src.items() if s["sourceType"] == "Unknown"])


if __name__ == "__main__":
    unittest.main()
