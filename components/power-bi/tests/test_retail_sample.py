"""Regression tests on the realistic Retail Sales sample (docs/ui-review-handover.md)."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "samples"))
from build_retail_sample import REPORT, build_retail_model, build_retail_payload  # noqa: E402
from pbidocgen.column_usage import build_column_usage  # noqa: E402

UNUSED = {("Customer", "City"), ("Customer", "Email"), ("Customer", "Phone"),
          ("Product", "Colour"), ("Product", "ListPrice"), ("Product", "SupplierCode"),
          ("Sales", "ETLBatchId"), ("Sales", "LegacyFlag"), ("Sales", "Margin"),
          ("Store", "Manager"), ("Store", "Store"), ("Targets", "Region"), ("Targets", "Target")}
# The broken binding Date[Calendar] names the Date table, so unused Date
# columns stay in Review: the binding could be any of them.
DATE_REVIEW = {("Date", "FiscalWeek"), ("Date", "Month")}


def decisions(columns):
    return {(r["table"], r["column"]): r for r in columns["rows"]}


class RetailSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = build_retail_model()
        cls.payload = build_retail_payload(cls.model)
        cls.rows = decisions(cls.payload["columns"])

    def test_unresolved_binding_only_holds_back_its_own_table(self):
        candidates = {k for k, r in self.rows.items() if r["decision"] == "Deletion candidate"}
        review = {k for k, r in self.rows.items() if r["decision"] == "Review"}
        self.assertEqual(candidates, UNUSED)
        self.assertEqual(review, DATE_REVIEW)
        for key in DATE_REVIEW:
            self.assertIn("Unresolved report binding Date[Calendar]", self.rows[key]["reviewNotes"])

    def test_unrelated_columns_carry_no_foreign_review_notes(self):
        # Impact inspector shows reviewNotes; Budget[Amount] must not mention Date.
        self.assertEqual(self.rows[("Budget", "Amount")]["reviewNotes"], [])
        self.assertEqual(self.payload["columns"]["tableIssues"], {"Date": ["Unresolved report binding Date[Calendar]"]})
        self.assertEqual(self.payload["columns"]["globalIssues"], [])
        self.assertIn("Unresolved report binding Date[Calendar]", self.payload["columns"]["issues"])

    def test_global_issues_still_block_every_candidate(self):
        report = copy.deepcopy(REPORT)
        report["warnings"] = [{"severity": "warning", "category": "Incomplete report",
                               "message": "Declared page 'x' is missing from the extract."}]
        rows = decisions(build_column_usage(self.model, report))
        self.assertFalse(any(r["decision"] == "Deletion candidate" for r in rows.values()))

    def test_missing_table_binding_is_reported_but_blocks_nothing(self):
        # Nope is not in the model, so Nope[X] cannot depend on any existing column.
        report = copy.deepcopy(REPORT)
        baseline = build_column_usage(self.model, copy.deepcopy(REPORT))
        report["pages"][0]["visuals"][0]["fields"].append(dict(table="Nope", field="X", kind="column"))
        analysis = build_column_usage(self.model, report)
        self.assertIn("Unresolved report binding Nope[X]", analysis["issues"])
        self.assertNotIn("Unresolved report binding Nope[X]", analysis["globalIssues"])
        count = lambda a: sum(r["decision"] == "Deletion candidate" for r in a["rows"])
        self.assertEqual(count(analysis), count(baseline))

    def test_bare_unresolved_binding_stays_global(self):
        report = copy.deepcopy(REPORT)
        report["pages"][0]["visuals"][0]["fields"].append(dict(table=None, field="Mystery", kind="column"))
        analysis = build_column_usage(self.model, report)
        self.assertFalse(any(r["decision"] == "Deletion candidate" for r in analysis["rows"]))

    def test_unused_targets_source_is_not_blocked_by_date_issue(self):
        rows = [r for r in self.payload["primarySources"]["rows"] if "Targets" in r["tables"]]
        self.assertTrue(rows)
        self.assertTrue(all(r["reportingStatus"] == "No reporting usage found" for r in rows))


    def measure(self, name, analysis=None):
        analysis = analysis or self.payload["columns"]
        return next(m for m in analysis["measures"] if m["measure"] == name)

    def test_unused_measure_is_a_deletion_candidate(self):
        self.assertEqual(self.payload["columns"]["measureCounts"], {"Keep": 13, "Deletion candidate": 2})
        self.assertEqual(self.measure("Revenue YTD")["decision"], "Deletion candidate")
        # Total Cost is only reached through Gross Margin, which the report uses.
        self.assertEqual(self.measure("Total Cost")["decision"], "Keep")

    def test_measure_used_only_by_unused_measures_names_them(self):
        model = copy.deepcopy(self.model)
        model["measures"].append(dict(model["measures"][0], name="YTD x2", expression="[Revenue YTD] * 2"))
        analysis = build_column_usage(model, REPORT)
        row = self.measure("Revenue YTD", analysis)
        self.assertEqual(row["decision"], "Deletion candidate")
        self.assertEqual(row["usedBy"], [row["table"] + "[YTD x2]"])
        self.assertIn("Remove them together", row["reason"])

    def test_measure_needed_by_model_roots_is_kept(self):
        model = copy.deepcopy(self.model)
        model["roles"][0]["tablePermissions"][0]["filterExpression"] = "[Revenue YTD] > 0"
        self.assertEqual(self.measure("Revenue YTD", build_column_usage(model, REPORT))["decision"], "Keep")

    def test_model_only_never_proposes_measure_deletion(self):
        analysis = build_column_usage(self.model, None)
        self.assertFalse(any(m["decision"] == "Deletion candidate" for m in analysis["measures"]))
        self.assertEqual(analysis["tables"], [])

    def test_whole_unused_table(self):
        self.assertEqual([t["table"] for t in self.payload["columns"]["tables"]], ["Targets"])

    def test_sources_have_one_name_on_every_screen(self):
        partitions = {pt["source"]["sourceType"] for t in self.payload["model"]["tables"] for pt in t["partitions"]}
        traced = {r["sourceType"] for r in self.payload["primarySources"]["rows"]}
        lineage = {r["sourceType"] for r in self.payload["linked"]["lineage"]}
        self.assertEqual(partitions, {"SQL Server", "SharePoint file", "CSV file"})
        self.assertEqual(traced, partitions)
        self.assertEqual(lineage, partitions)
        labels = {r["sourceLabel"] for r in self.payload["linked"]["lineage"]}
        self.assertEqual(labels, {"SQL Server \u00b7 finance-sql.corp.local / FinanceDW",
                                  "SharePoint file \u00b7 Budget FY26.xlsx", "CSV file \u00b7 targets.csv"})


class SourceLabelTests(unittest.TestCase):
    def test_refine(self):
        from pbidocgen.source_labels import refine_source_type as r
        self.assertEqual(r("Web", "https://contoso.sharepoint.com/sites/x/Shared Documents/a.xlsx"), "SharePoint file")
        self.assertEqual(r("Web / API", "https://contoso.sharepoint.com/sites/x/_api/web/lists"), "Web / API")
        self.assertEqual(r("Web", "https://example.org/api"), "Web / API")
        self.assertEqual(r("File", "C:\\data\\a.CSV"), "CSV file")
        self.assertEqual(r("File", "\\\\srv\\share\\b.xlsx"), "Excel workbook")
        self.assertEqual(r("File", "C:\\data\\c.json"), "JSON file")
        self.assertEqual(r("File", "C:\\data\\d.bin"), "File")
        self.assertEqual(r("SQL Server", ""), "SQL Server")

    def test_label(self):
        from pbidocgen.source_labels import source_label
        self.assertEqual(source_label(dict(sourceType="SharePoint file",
                         detail="https://c.sharepoint.com/s/Shared%20Documents/Budget.xlsx")), "SharePoint file \u00b7 Budget.xlsx")
        self.assertEqual(source_label(dict(sourceType="SQL Server", server="srv", database="db")), "SQL Server \u00b7 srv / db")
        self.assertEqual(source_label(dict(sourceType="Unknown")), "Unknown")

    def test_traced_sharepoint_document_is_a_file(self):
        from pbidocgen.external_sources import external_value
        from pbidocgen.m_sources import Value

        class T:
            current_query = "Q"
        url = "https://contoso.sharepoint.com/sites/Finance/Shared Documents/Budget FY26.xlsx"
        v = external_value(T(), "Web.Contents", [Value(kind="text", text=url)])
        self.assertEqual(v.objects[0]["sourceType"], "SharePoint file")
        self.assertEqual(v.objects[0]["object"], "Budget FY26.xlsx")

class QualityTests(unittest.TestCase):
    def test_normalise_ignores_formatting_but_not_strings(self):
        from pbidocgen.quality import normalise_dax as n
        self.assertEqual(n("CALCULATE( [Revenue],\n  SAMEPERIODLASTYEAR('Date'[Date])) -- note"),
                         n("calculate([revenue],sameperiodlastyear(Date[date]))"))
        self.assertNotEqual(n('IF([x], "Yes")'), n('IF([x], "YES")'))
        self.assertNotEqual(n("SUM(Sales[Qty])"), n("SUM(Sales[Qty2])"))

    def test_sample_duplicates_and_coverage(self):
        q = build_retail_payload()["quality"]
        self.assertEqual([g["measures"] for g in q["duplicateMeasures"]], [["Sales[Revenue]", "Sales[Net Sales]"]])
        self.assertEqual(q["duplicateMeasures"][0]["formats"], ["#,0", ""])
        self.assertEqual(q["duplicateMeasures"][0]["match"], "Same DAX apart from formatting, comments or letter case")
        d = q["documentation"]
        self.assertEqual((d["measures"], d["measuresDescribed"], d["measuresInFolders"]), (15, 2, 6))
        self.assertEqual(len(d["measuresWithoutFormat"]), 12)
        self.assertIn("Sales[Net Sales]", d["measuresWithoutFormat"])
        self.assertNotIn("Sales[Revenue]", d["measuresWithoutFormat"])

    def test_model_only_has_quality_and_report_only_does_not(self):
        from pbidocgen.renderer import build_payload
        self.assertIsNotNone(build_payload(build_retail_model(), None, None, "M")["quality"])
        self.assertIsNone(build_payload(None, REPORT, None, "R")["quality"])


if __name__ == "__main__":
    unittest.main()
