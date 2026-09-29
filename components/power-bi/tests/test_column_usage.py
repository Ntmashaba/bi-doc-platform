import csv
import json
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path

from generate_docs import main
from pbidocgen.column_usage import build_column_usage, csv_cell, write_column_csv
from pbidocgen.model_parser import parse_model
from pbidocgen.report_parser import parse_report
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.linker import link


def raw_model():
    return {"model": {"name": "Sales", "tables": [{
        "name": "Sales", "columns": [
            {"name": "Amount", "sourceColumn": "NetAmount"},
            {"name": "Unused"}, {"name": "Key"}, {"name": "Region"},
            {"name": "Sort"}, {"name": "Label", "sortByColumn": "Sort"},
            {"name": "BookmarkOnly"}, {"name": "Format"},
            {"name": "Double", "type": "calculated", "expression": "[Amount] * 2"}],
        "hierarchies": [{"name": "Geography", "levels": [{"name": "Area", "column": "Region"}]}],
        "measures": [{"name": "Base", "expression": "SUM(Sales[Amount])"},
                     {"name": "Total", "expression": "VAR x = 1 RETURN [Base] * x"},
                     {"name": "Formatted", "expression": "1", "formatStringDefinition": {
                         "expression": "SELECTEDVALUE(Sales[Format])"}}],
        "partitions": [{"name": "Import", "source": {"type": "m", "expression":
            'let S = Sql.Database("server", "db"), T = S{[Schema="dbo",Item="Orders"]}[Data] in T'}}]
    }, {"name": "Dim", "columns": [{"name": "ID"}], "measures": []}],
        "relationships": [{"name": "join", "fromTable": "Sales", "fromColumn": "Key",
                           "toTable": "Dim", "toColumn": "ID", "isActive": False}],
        "roles": [{"name": "Regional", "tablePermissions": [{"name": "Sales",
                   "filterExpression": '[Region] = "https://region"'}]}]}}


def field(column, kind="column", table="Sales", **kwargs):
    return dict(table=table, field=column, kind=kind, **kwargs)


def report_fixture():
    def page(pid, refs):
        return {"id": pid, "name": "Same / page", "filters": [], "visuals": [{
            "id": "v1", "type": "table", "title": None, "fields": refs, "filters": []}]}
    return {"name": "Sales", "pages": [page("p1", [field("Amount"), field("Total", "measure")]),
            page("p2", [field("Total", "measure")])], "reportFilters": [],
            "bookmarks": [{"name": "Saved", "fields": [field("BookmarkOnly")]}],
            "manifest": [], "warnings": []}


class ColumnUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def model(self, raw=None):
        path = self.root / "model.bim"
        path.write_text(json.dumps(raw or raw_model()), encoding="utf-8")
        return parse_model(path)

    def test_bare_column_in_measure_binds_to_its_own_table(self):
        raw = {"model": {"name": "M", "tables": [
            {"name": t, "columns": [{"name": "Revenue"}], "measures": [
                {"name": "Total", "expression": "SUM([Revenue])"}] if t == "Calc" else []}
            for t in ("Fact A", "Fact B", "Calc")], "relationships": []}}
        analysis = build_column_usage(self.model(raw), None)
        self.assertFalse([i for i in analysis["globalIssues"] if "Ambiguous" in i])
        self.assertFalse(any(analysis.get("tableIssues", {}).values()))

    def rows(self, column, model=None, report=None):
        analysis = build_column_usage(model or self.model(), report or report_fixture())
        return [r for r in analysis["rows"] if r["column"] == column]

    def test_transitive_measures_and_stable_page_grain(self):
        rows = self.rows("Amount")
        self.assertEqual([r["pageId"] for r in rows], ["p1", "p2"])
        self.assertEqual(rows[0]["pageUsage"], "Direct + Via measures")
        self.assertEqual(rows[1]["pageUsage"], "Via measures")
        self.assertEqual(rows[1]["pageMeasures"], ["Sales[Total]"])
        self.assertEqual(rows[0]["usedByMeasures"], ["Sales[Base]", "Sales[Total]"])
        self.assertEqual(rows[0]["usedByCalculations"], ["Sales[Double]"])
        self.assertEqual(rows[0]["sourceColumn"], "NetAmount")
        self.assertIn("server / db / dbo / Orders", rows[0]["source"][0])

    def test_non_page_columns_retained_and_candidates_scoped(self):
        row = self.rows("Unused")[0]
        self.assertEqual(row["pageId"], "")
        self.assertEqual(row["decision"], "Deletion candidate")
        self.assertIn("other reports", row["scope"])
        row = self.rows("BookmarkOnly")[0]
        self.assertEqual(row["decision"], "Keep")
        self.assertEqual(row["pageId"], "")
        self.assertEqual(row["usedInReport"], "Yes")

    def test_internal_dependencies_block_deletion(self):
        for column, evidence in [("Key", "Relationship"), ("Region", "RLS"),
                                 ("Sort", "Sort-by"), ("Format", "Measure")]:
            row = self.rows(column)[0]
            self.assertEqual(row["decision"], "Keep", column)
            self.assertTrue(any(evidence in s for s in row["modelDependencies"]))
            self.assertEqual(row["usedInReport"], "Not detected")

    def test_report_filters_apply_to_every_page_and_hierarchy_resolves(self):
        report = report_fixture()
        report["reportFilters"] = [field("Label")]
        report["pages"][0]["visuals"][0]["fields"].append(field("Area", "hierarchyLevel", hierarchy="Geography"))
        self.assertEqual(len(self.rows("Label", report=report)), 2)
        self.assertEqual(self.rows("Region", report=report)[0]["pageUsage"], "Direct")

    def test_calculated_column_usage_and_cycles(self):
        raw = raw_model()
        raw["model"]["tables"][0]["measures"] += [
            {"name": "A", "expression": "[B] + SUM(Sales[Double])"},
            {"name": "B", "expression": "[A]"}]
        report = report_fixture()
        report["pages"][0]["visuals"][0]["fields"] = [field("A", "measure")]
        rows = self.rows("Amount", model=self.model(raw), report=report)
        self.assertIn("Sales[A]", rows[0]["pageMeasures"])
        self.assertIn("Sales[B]", rows[0]["usedByMeasures"])

    def test_whole_table_and_incomplete_analysis_are_review(self):
        raw = raw_model()
        # Distinct rows depend on every column; a plain row count depends on none.
        raw["model"]["tables"][0]["measures"].append({"name": "Rows", "expression": "COUNTROWS(DISTINCT(Sales))"})
        row = self.rows("Unused", model=self.model(raw))[0]
        self.assertEqual(row["decision"], "Review")
        self.assertTrue(any("Whole-table" in s for s in row["reviewNotes"]))
        counted = raw_model()
        counted["model"]["tables"][0]["measures"].append({"name": "Rows", "expression": "CALCULATE(COUNTROWS('Sales'), Sales[Region] = \"x\")"})
        self.assertFalse(any("Whole-table" in s for s in self.rows("Unused", model=self.model(counted))[0]["reviewNotes"]))
        report = report_fixture()
        report["warnings"] = [{"message": "Could not parse visual"}]
        self.assertEqual(self.rows("Unused", report=report)[0]["decision"], "Review")
        report["pages"][0]["visuals"][0]["fields"].append(field("Missing"))
        self.assertTrue(any("Unresolved report" in s for s in self.rows("Unused", report=report)[0]["reviewNotes"]))

    def test_model_only_never_proposes_deletion(self):
        analysis = build_column_usage(self.model(), None)
        self.assertFalse(any(r["decision"] == "Deletion candidate" for r in analysis["rows"]))
        self.assertTrue(all(r["usedInReport"] == "Unknown" for r in analysis["rows"]))

    def test_dax_case_escaping_strings_and_additional_roots(self):
        raw = raw_model()
        tbl = raw["model"]["tables"][0]
        tbl["columns"].append({"name": "Cost]Tax"})
        tbl["measures"].append({"name": "Escaped", "expression":
            'IF("https://example/[Unused]" = "x", SUM(sales[Cost]]Tax]), 0) // Sales[Unused]'})
        tbl["calculationGroup"] = {"calculationItems": [{"name": "Item", "expression": "SUM(Sales[Label])"}]}
        parsed = self.model(raw)
        self.assertEqual(self.rows("Cost]Tax", model=parsed)[0]["decision"], "Keep")
        self.assertFalse(any("Unused" in s for s in self.rows("Unused", model=parsed)[0]["usedByMeasures"]))
        self.assertIn("Calculation group structure", self.rows("Unused", model=parsed)[0]["modelDependencies"])
        self.assertEqual(self.rows("Label", model=parsed)[0]["decision"], "Keep")

    def test_tmdl_source_and_format_expression(self):
        definition = self.root / "Example.SemanticModel" / "definition"
        (definition / "tables").mkdir(parents=True)
        (definition / "model.tmdl").write_text("model Model\n", encoding="utf-8")
        (definition / "tables" / "Sales.tmdl").write_text(
            "table Sales\n\tcolumn Amount\n\t\tdataType: decimal\n\t\tsourceColumn: NetAmount\n"
            "\tmeasure Total = SUM(Sales[Amount])\n"
            "\t\tformatStringDefinition = SELECTEDVALUE(Sales[Amount])\n", encoding="utf-8")
        model = parse_model(definition.parent)
        self.assertEqual(model["tables"][0]["columns"][0]["sourceColumn"], "NetAmount")
        self.assertIn("SELECTEDVALUE", model["measures"][0]["formatStringExpression"])

    def test_real_pbir_parsing_and_cli_exports(self):
        self.model()
        definition = self.root / "Sales.Report" / "definition"
        visual = definition / "pages" / "p1" / "visuals" / "v1"
        visual.mkdir(parents=True)
        (definition / "report.json").write_text('{"name":"Sales"}')
        (definition / "pages" / "p1" / "page.json").write_text('{"displayName":"Summary"}')
        binding = {"Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": "Total"}}
        # A dynamic title outside queryState must be counted.
        (visual / "visual.json").write_text(json.dumps({"visual": {
            "visualType": "card", "query": {"queryState": {"Values": []}}, "objects": {"title": binding}}}))
        parsed = parse_report(definition.parent)
        self.assertEqual(parsed["pages"][0]["visuals"][0]["fields"][0]["field"], "Total")
        result = main(["--model", str(self.root / "model.bim"), "--report", str(definition.parent),
                       "--output", str(self.root / "out.html"), "--csv", str(self.root / "out.csv"),
                       "--json", str(self.root / "out.json")])
        self.assertEqual(result, 0)
        payload = json.loads((self.root / "out.json").read_text())
        self.assertTrue(next(m for m in payload["model"]["measures"] if m["name"] == "Base")["usedInReport"])
        with (self.root / "out.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(payload["columns"]["rows"]))
        self.assertIn("Sales[Total]", next(r for r in rows if r["Column"] == "Amount")["Measures on this page"])

    def test_csv_unicode_quoting_and_formula_cells(self):
        analysis = build_column_usage(self.model(), report_fixture())
        analysis["rows"][0]["column"] = '=SUM(1,2)'
        analysis["rows"][0]["page"] = 'Café, "summary"\nsecond line'
        path = write_column_csv(analysis, self.root / "columns.csv")
        with path.open(encoding="utf-8-sig", newline="") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["Column"], "'=SUM(1,2)")
        self.assertEqual(row["Report page"], 'Café, "summary"\nsecond line')
        self.assertEqual(csv_cell("  @value"), "'  @value")

    @unittest.skipUnless(shutil.which("node"), "Node is needed only for HTML script checks")
    def test_html_script_and_browser_csv_match_python(self):
        model, report = self.model(), report_fixture()
        payload = build_payload(model, report, link(model, report), "Column review")
        html = render_html(payload, self.root / "out.html")
        browser_csv = self.root / "browser.csv"
        result = subprocess.run(["node", str(Path(__file__).with_name("check_columns_ui.cjs")),
                                 str(html), str(browser_csv)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload["columns"]["rows"][0]["column"] = '<img src=x onerror=alert(1)>'
        payload["columns"]["rows"][0]["page"] = '=1+1'
        python_csv = write_column_csv(payload["columns"], self.root / "python.csv")
        def read(path):
            with path.open(encoding="utf-8-sig", newline="") as f:
                return list(csv.reader(f))
        self.assertEqual(read(browser_csv), read(python_csv))


if __name__ == "__main__":
    unittest.main()
