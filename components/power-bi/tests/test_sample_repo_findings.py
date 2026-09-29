"""Regressions found by running the pbi-tools/pbix-samples extracts (29 reports)."""
import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pbidocgen.legacy_mashup import members, section_text
from pbidocgen.linker import link
from pbidocgen.m_sources import Tracer, materialize
from pbidocgen.model_parser import inline_legacy_mashups, parse_model
from pbidocgen.pbitools_folder import assemble
from pbidocgen.report_parser import _collect_aliases, _collect_field_refs

SECTION = 'section Section1;\n\nshared Age = let\n    Source = Excel.Workbook(File.Contents("C:\\\\a;b.xlsx"), null, true)\nin\n    Source;\n\nshared #"Other Q" = 1;\n'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


class FolderLayoutTests(unittest.TestCase):
    def test_folder_model_is_reassembled(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d)
            write(m / "database.json", {"name": "M", "model": {"relationships": [], "expressions": [{"name": "P", "kind": "m"}]}})
            write(m / "tables/Sales/table.json", {"name": "Sales", "partitions": [{"name": "p", "source": {"type": "m"}}]})
            write(m / "tables/Sales/columns/Amount.json", {"name": "Amount", "dataType": "double"})
            write(m / "tables/Sales/columns/Double.json", {"name": "Double", "type": "calculated"})
            write(m / "tables/Sales/columns/Double.dax", "[Amount] * 2")
            write(m / "tables/Sales/measures/Total.json", {"name": "Total"})
            write(m / "tables/Sales/measures/Total.dax", "SUM(Sales[Amount])")
            write(m / "tables/Sales/measures/Old.xml", '<Measure Name="Old"><Expression><![CDATA[1]]></Expression></Measure>')
            write(m / "tables/Cal/Cal.json", {"name": "Cal", "partitions": [{"name": "c", "source": {"type": "calculated"}}]})
            write(m / "tables/Cal/Cal.dax", "CALENDARAUTO()")
            write(m / "queries/Sales.m", "let S = 1 in S")
            write(m / "queries/P.m", '"x"')
            model = assemble(m)["model"]
        tables = {t["name"]: t for t in model["tables"]}
        self.assertEqual(tables["Sales"]["partitions"][0]["source"]["expression"], "let S = 1 in S")
        self.assertEqual({c["name"]: c.get("expression") for c in tables["Sales"]["columns"]}, {"Amount": None, "Double": "[Amount] * 2"})
        self.assertEqual({x["name"]: x["expression"] for x in tables["Sales"]["measures"]}, {"Total": "SUM(Sales[Amount])", "Old": "1"})
        self.assertEqual(tables["Cal"]["partitions"][0]["source"]["expression"], "CALENDARAUTO()")
        self.assertEqual(model["expressions"][0]["expression"], '"x"')


class LegacyMashupTests(unittest.TestCase):
    def package(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("Formulas/Section1.m", SECTION)
        return base64.b64encode(b"\x00\x00\x00\x00" + buffer.getvalue()).decode()

    def test_section_members_respect_strings(self):
        found = members(SECTION)
        self.assertEqual(set(found), {"Age", "Other Q"})
        self.assertIn("a;b.xlsx", found["Age"])

    def test_placeholder_query_becomes_its_power_query(self):
        ds = {"name": "g", "connectionString": f"Provider=Microsoft.PowerBI.OleDb;Mashup={self.package()};Location=Age"}
        self.assertEqual(set(members(section_text(ds))), {"Age", "Other Q"})
        model = {"dataSources": [ds], "tables": [{"name": "Age", "partitions": [
            {"name": "p", "source": {"query": "SELECT * FROM [Age]", "dataSource": "g"}}]}]}
        inline_legacy_mashups(model)
        source = model["tables"][0]["partitions"][0]["source"]
        self.assertEqual(source["type"], "m")
        self.assertIn("Excel.Workbook", source["expression"])
        self.assertEqual([e["name"] for e in model["expressions"]], ["Other Q"])


class TmdlLegacySourceTests(unittest.TestCase):
    def test_tmdl_data_source_mashup_is_inlined(self):
        package = LegacyMashupTests().package()
        with tempfile.TemporaryDirectory() as d:
            m = Path(d)
            write(m / "model.tmdl", "model Model\n\tculture: en-US\n")
            write(m / "dataSources.tmdl", "dataSource g = provider\n\tconnectionString: "
                  f'provider=Microsoft.PowerBI.OleDb;mashup="{package}";location=Age\n')
            write(m / "tables" / "Age.tmdl", "table Age\n\tcolumn A\n\n\tpartition Age-1 = query\n\t\tmode: import\n"
                  "\t\tsource\n\t\t\tdataSource: g\n")
            model = parse_model(m)
        source = model["tables"][0]["partitions"][0]["source"]
        self.assertEqual((source["sourceType"], source["object"]), ("Excel workbook", "a;b.xlsx"))


class TracerTests(unittest.TestCase):
    def issues(self, code):
        value = Tracer().trace(code, "Q")
        return [i for i in value.issues if "incomplete" in i or "Unresolved" in i or "Unsupported" in i], materialize(value)

    def test_auto_removed_columns_step_and_helpers_are_not_gaps(self):
        issues, rows = self.issues('''let S = Sql.Database("s", "d", [Query="select a from t"]),
            N = Table.InsertRows(S, 0, {[a = -1]}),
            P = Table.AddColumn(N, "k", each Text.PadStart(Number.ToText([a]), 3, "0")),
            X = Csv.Document(Text.Combine({"a"}), [Delimiter=",", QuoteStyle=QuoteStyle.Csv]),
            A = let t = Table.FromValue(P, [DefaultColumnName = "a"]),
                    r = Table.RemoveColumns(t, Table.ColumnsOfType(t, {type table, type record, type list}))
                in Table.TransformColumnNames(r, Text.Clean)
        in A''')
        self.assertEqual(issues, [])
        self.assertEqual({r["sourceType"] for r in rows}, {"SQL Server"})


class ReportTests(unittest.TestCase):
    def test_inline_and_aliased_subquery_outputs_are_not_fields(self):
        sub = {"Subquery": {"Query": {"From": [{"Name": "o", "Entity": "Sales"}],
                                      "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "o"}}, "Property": "Amount"}}]}}}
        node = {"a": {"Column": {"Expression": sub, "Property": "V1"}},
                "b": {"From": [{"Name": "q", "Expression": sub, "Type": 2}]},
                "c": {"Column": {"Expression": {"SourceRef": {"Source": "q"}}, "Property": "V2"}}}
        aliases, refs = {}, []
        _collect_aliases(node, aliases)
        _collect_field_refs(node, aliases, refs)
        self.assertEqual({(r["table"], r["field"]) for r in refs}, {("Sales", "Amount")})

    def test_hierarchy_level_resolves_to_its_column(self):
        with tempfile.TemporaryDirectory() as d:
            bim = Path(d) / "model.bim"
            write(bim, {"model": {"tables": [{"name": "Date", "columns": [{"name": "Fiscal Year"}], "hierarchies": [
                {"name": "Fiscal", "levels": [{"name": "Year", "column": "Fiscal Year"}]}]}]}})
            model = parse_model(bim)
        report = {"name": "R", "pages": [{"id": "p", "name": "P", "filters": [], "visuals": []}], "bookmarks": [],
                  "reportFilters": [], "manifest": [{"table": "Date", "field": "Year", "kind": "hierarchyLevel",
                                                     "hierarchy": "Fiscal", "locations": []}]}
        warnings = [w for w in link(model, report)["warnings"] if w["category"] == "Broken binding"]
        self.assertEqual(warnings, [])


class OpenItemTests(unittest.TestCase):
    def test_formatting_only_reference_is_named_as_such(self):
        from pbidocgen.linker import _broken
        entry = {"locations": [{"page": "P", "description": "Formatting rule in visual: Bar"}]}
        self.assertTrue(_broken(entry, "T[F]", "x").startswith("A formatting rule refers to T[F]"))
        entry["locations"].append({"page": "P", "description": "Visual: Bar"})
        self.assertTrue(_broken(entry, "T[F]", "x").startswith("Report references T[F]"))

    def test_fields_under_objects_are_formatting(self):
        refs = []
        col = {"Column": {"Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": "Colour"}}
        _collect_field_refs({"singleVisual": {"objects": {"dataPoint": [col]}, "projections": [col]}}, {}, refs, "q")
        self.assertEqual(sorted(r["context"] for r in refs), ["formatting", "q"])

    def test_missing_report_json_does_not_block_cleanup(self):
        from pbidocgen.report_parser import parse_report
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "S.Report" / "definition"
            write(root / "pages" / "pages.json", {"pageOrder": ["p"]})
            write(root / "pages" / "p" / "page.json", {"displayName": "P"})
            report = parse_report(root.parent)
        self.assertEqual([(w["severity"], w["category"]) for w in report["warnings"]], [("info", "Report settings missing")])

    def test_custom_visual_display_names(self):
        from pbidocgen.custom_visuals import from_folder, from_pbix
        package = {"visual": {"name": "wordCloud", "displayName": "WordCloud 1.7.1"}}
        with tempfile.TemporaryDirectory() as d:
            write(Path(d) / "CustomVisuals" / "WordCloud1447959067750" / "package.json", package)
            self.assertEqual(from_folder(d), {"WordCloud1447959067750": "WordCloud"})
            pbix = Path(d) / "r.pbix"
            with zipfile.ZipFile(pbix, "w") as z:
                z.writestr("Report/CustomVisuals/PBI_CV_1/package.json", json.dumps({"visual": {"displayName": "Play Axis"}}))
            self.assertEqual(from_pbix(pbix), {"PBI_CV_1": "Play Axis"})

if __name__ == "__main__":
    unittest.main()
