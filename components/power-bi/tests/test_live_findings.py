"""Regressions found by the live test on real PBIX/PBIP files (docs/pbix-live-test-handover.md)."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pbidocgen.column_usage import build_column_usage
from pbidocgen.extracted_report import parse_extracted_report, parse_legacy_layout
from pbidocgen.linker import _used_on
from pbidocgen.input_validation import validate_model, validate_report
from pbidocgen.pbix_batch import extract_pbir, load_extracted
from pbidocgen.report_parser import _collect_field_refs, parse_report


def col(table, prop, source=None):
    expr = {"SourceRef": {"Source": source}} if source else {"SourceRef": {"Entity": table}}
    return {"Column": {"Expression": expr, "Property": prop}}


def model(tables, measures=()):
    return {"tables": [{"name": t, "columns": [{"name": c} for c in cols], "measures": [], "partitions": [],
                        "hierarchies": []} for t, cols in tables.items()],
            "measures": [dict(m, table=m.get("table", next(iter(tables)))) for m in measures],
            "relationships": [], "roles": [], "warnings": []}


class ValidationTests(unittest.TestCase):
    def test_report_metadata_keys_are_not_visual_parts(self):
        # Power BI Desktop 2025+ writes this in every report.json.
        validate_report({"themeCollection": {"baseTheme": {"reportVersionAtImport": {"visual": "2.12.0"}}}})
        with self.assertRaises(ValueError):
            validate_report({"visual": "not an object"})

    def test_visual_calculation_dax_is_text(self):
        validate_report({"visual": {"query": {"queryState": {"Values": {"projections": [
            {"field": {"NativeVisualCalculation": {"Expression": "ROUND([MTTR] - [target], 1)", "Name": "diff"}}}]}}}}})

    def test_model_without_name_and_free_form_payloads(self):
        validate_model({"model": {"name": None, "tables": [{"name": "T", "extendedProperties": [
            {"name": "P", "value": {"name": 1}}]}]}})
        with self.assertRaises(ValueError):
            validate_model({"model": {"tables": [{"name": 1}]}})


class ReportLayoutTests(unittest.TestCase):
    def test_forecast_outputs_are_not_model_fields(self):
        out = []
        _collect_field_refs({"Select": [{"Column": {"Expression": {"TransformTableRef": {"Source": "output0"}},
                                                    "Property": "forecastValue"}}, col("Calendar", "Date")]}, {}, out)
        self.assertEqual([(f["table"], f["field"]) for f in out], [("Calendar", "Date")])

    def test_textbox_query_label_without_source_is_not_a_binding(self):
        refs = []
        _collect_field_refs({"Column": {"Expression": {}, "Property": "IT Area.IT Sub Area ID"}}, {}, refs)
        self.assertEqual(refs, [])

    def test_aliases_are_scoped_to_each_query(self):
        col = lambda src, prop: {"Column": {"Expression": {"SourceRef": {"Source": src}}, "Property": prop}}
        node = {"a": {"From": [{"Name": "o", "Entity": "Opportunities"}], "Select": [col("o", "Value")]},
                "b": {"From": [{"Name": "o", "Entity": "Owners"}], "Select": [col("o", "Owner")]},
                "c": {"From": [{"Name": "q", "Expression": {"Subquery": {}}, "Type": 2}],
                      "Select": [col("q", "Opportunity Forecast Adjustment.Forecast Adjustment Value")]}}
        refs = []
        _collect_field_refs(node, {"o": "Owners"}, refs)
        self.assertEqual({(r["table"], r["field"]) for r in refs},
                         {("Opportunities", "Value"), ("Owners", "Owner")})

    def test_broken_binding_says_where_it_is_used(self):
        entry = {"locations": [{"page": "Pipeline", "description": "Page filter"}] * 2
                 + [{"page": None, "description": "Bookmark: A"}, {"page": "X", "description": "Visual: a"},
                    {"page": "Y", "description": "Visual: b"}]}
        self.assertEqual(_used_on(entry), " Used on: Pipeline — Page filter; No specific page — Bookmark: A;"
                                          " X — Visual: a (+1 more).")
        self.assertEqual(_used_on({}), "")

    def test_auto_date_hierarchy_uses_its_date_column(self):
        out = []
        _collect_field_refs({"HierarchyLevel": {"Expression": {"Hierarchy": {"Expression": {"PropertyVariationSource": {
            "Expression": {"SourceRef": {"Entity": "Calendar"}}, "Name": "Variation", "Property": "Date"}},
            "Hierarchy": "Date Hierarchy"}}, "Level": "Quarter"}}, {}, out)
        self.assertEqual([(f["table"], f["field"], f["kind"]) for f in out], [("Calendar", "Date", "column")])

    def layout(self):
        visual = {"x": 1, "y": 2, "width": 3, "height": 4, "config": json.dumps({"name": "v1", "singleVisual": {
            "visualType": "tableEx", "prototypeQuery": {"From": [{"Name": "s", "Entity": "Sales"}],
                                                        "Select": [col("Sales", "Amount", "s")]}}})}
        qna = {"x": 0, "y": 0, "width": 1, "height": 1, "config": json.dumps({"name": "q1", "singleVisual": {
            "visualType": "qnaVisual", "prototypeQuery": {"From": [{"Name": "s", "Entity": "Sales"}],
                                                          "Select": [col("Sales", "Dates", "s")]}}})}
        return {"config": json.dumps({"bookmarks": [{"displayName": "B1", "explorationState": {}},
                                                    {"displayName": "Group", "children": [{"displayName": "B2"}]}]}),
                "sections": [{"name": "p2", "displayName": "Second", "ordinal": 1, "visualContainers": [qna]},
                             {"name": "p1", "displayName": "First", "ordinal": 0, "visualContainers": [visual]}]}

    def test_single_file_legacy_report_json(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "Sales.Report"
            folder.mkdir()
            (folder / "report.json").write_text(json.dumps(self.layout()))
            report = parse_report(folder)
        self.assertEqual([p["name"] for p in report["pages"]], ["First", "Second"])
        self.assertEqual(report["pages"][0]["visuals"][0]["fields"][0]["field"], "Amount")
        self.assertEqual([b["name"] for b in report["bookmarks"]], ["B1", "B2"])
        self.assertEqual(report["warnings"][0]["category"], "Legacy report layout")

    def test_qna_answers_are_not_broken_bindings(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "report.json").write_text(json.dumps(self.layout()))
            report = parse_legacy_layout(Path(d), "Sales")
        report["warnings"] = []
        analysis = build_column_usage(model({"Sales": ["Amount", "Old"]}), report)
        self.assertFalse(any("Dates" in i for i in analysis["issues"]))

    def test_legacy_layout_caution_is_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "report.json").write_text(json.dumps(self.layout()))
            report = parse_legacy_layout(Path(d), "Sales")
        rows = lambda r: {x["column"]: x["decision"] for x in build_column_usage(model({"Sales": ["Amount", "Old"]}), r)["rows"]}
        self.assertEqual(rows(report)["Old"], "Deletion candidate")
        # A visual with a data query we could not read blocks every candidate.
        report["pages"][0]["visuals"].append({"id": "cv", "type": "PBI_CV_0123", "title": None, "fields": [],
                                              "filters": [], "unreadableBindings": True})
        self.assertEqual(rows(report)["Old"], "Review")

    def test_pbi_tools_bookmark_folders_are_one_bookmark_each(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "report.json").write_text("{}")
            page = root / "sections" / "000_Page"
            (page / "visualContainers" / "v").mkdir(parents=True)
            (page / "section.json").write_text(json.dumps({"name": "p", "displayName": "Page"}))
            (page / "visualContainers" / "v" / "visualContainer.json").write_text(json.dumps({"config": "{}"}))
            for name in ("Intro", "Detail"):
                states = root / "bookmarks" / name / "sections" / "p" / "visualContainers"
                states.mkdir(parents=True)
                (root / "bookmarks" / name / "bookmark.json").write_text(json.dumps({"displayName": name}))
                for i in range(5):
                    (states / f"{i}.json").write_text("{}")
            report = parse_extracted_report(root, "R")
        self.assertEqual(sorted(b["name"] for b in report["bookmarks"]), ["Detail", "Intro"])


class DaxTests(unittest.TestCase):
    def test_row_context_and_local_columns(self):
        m = model({"Events": ["component", "name"], "EventTypes": ["component"], "Metadata": ["Path"]}, [
            {"name": "Count", "table": "Metadata", "expression": "COUNTROWS(FILTER('Events', [component] = \"DSE\" && [name] = \"x\"))"},
            {"name": "Corr", "table": "Metadata", "expression": 'VAR t = ADDCOLUMNS(Events, "__x", 1) RETURN AVERAGEX(t, [__x])'}])
        analysis = build_column_usage(m, None)
        self.assertEqual([i for i in analysis["issues"] if "DAX" in i], [])
        used = {(r["table"], r["column"]) for r in analysis["rows"] if r["usedByMeasures"]}
        self.assertIn(("Events", "component"), used)
        self.assertNotIn(("EventTypes", "component"), used)


class PbixTests(unittest.TestCase):
    def test_pbir_is_read_from_the_pbix(self):
        with tempfile.TemporaryDirectory() as d:
            pbix = Path(d) / "New.pbix"
            with zipfile.ZipFile(pbix, "w") as z:
                z.writestr("Report/definition/report.json", json.dumps({"themeCollection": {"baseTheme": {"reportVersionAtImport": {"visual": "2.12.0"}}}}))
                z.writestr("Report/definition/pages/pages.json", json.dumps({"pageOrder": ["p1"]}))
                z.writestr("Report/definition/pages/p1/page.json", json.dumps({"displayName": "Overview"}))
            model_dir = Path(d) / "extracted" / "Model"
            model_dir.mkdir(parents=True)
            (model_dir / "database.json").write_text(json.dumps({"model": {"tables": []}}))
            model_, report = load_extracted(Path(d) / "extracted", pbix, True)
        self.assertEqual(model_["name"], "New")
        self.assertEqual([p["name"] for p in report["pages"]], ["Overview"])
        self.assertFalse(report["warnings"])

    def test_unsafe_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            pbix = Path(d) / "Bad.pbix"
            with zipfile.ZipFile(pbix, "w") as z:
                z.writestr("Report/definition/pages/p/page.json", "{}")
                z.writestr("Report/definition/../../evil.txt", "x")
            with self.assertRaises(ValueError):
                extract_pbir(pbix, Path(d) / "out")
            self.assertFalse((Path(d) / "evil.txt").exists())


if __name__ == "__main__":
    unittest.main()
