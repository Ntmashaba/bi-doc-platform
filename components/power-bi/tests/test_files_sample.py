"""SharePoint lists/files and Windows file/folder sources via the synthetic Field
Services sample (tests/samples/build_files_sample.py)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "samples"))
from build_files_sample import build_files_payload, build_model  # noqa: E402

SITE = "https://contoso.sharepoint.com/sites/FieldOps"


class FilesSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = build_model()
        cls.payload = build_files_payload(cls.model)
        cls.sources = {t["name"]: t["partitions"][0]["source"] for t in cls.model["tables"]}

    def source(self, table):
        s = self.sources[table]
        return s["sourceType"], s.get("object"), s.get("detail"), s.get("traceStatus")

    def test_sharepoint_lists(self):
        self.assertEqual(self.source("Work Orders"), ("SharePoint list", "Work Orders", SITE, "Resolved"))
        self.assertEqual(self.source("Technician")[:2], ("SharePoint list", "7c1b0c9e-2f0a-4c7b-9d1e-3a5b6c7d8e9f"))
        self.assertEqual(self.source("Site"), ("SharePoint list", "Sites", "https://sp2019.corp.example/sites/Assets", "Resolved"))

    def test_sharepoint_files(self):
        self.assertEqual(self.source("Budget"), ("SharePoint file", "Budget FY26.xlsx",
                                                 SITE + "/Shared Documents/Finance/Budget FY26.xlsx", "Resolved"))
        self.assertEqual(self.source("Price List"), ("SharePoint file", "Price List.csv",
                                                     SITE + "/Shared Documents/Procurement/Price List.csv", "Resolved"))
        self.assertEqual(self.source("SLA Targets")[:2], ("SharePoint file", "SLA Targets.xlsx"))

    def test_windows_files(self):
        self.assertEqual(self.source("Parts"), ("Excel workbook", "Parts Catalogue.xlsx",
                                                "C:\\Data\\FieldOps\\Parts Catalogue.xlsx", "Resolved"))
        self.assertEqual(self.source("Vehicle"), ("CSV file", "vehicles.csv",
                                                  "\\\\fs01.corp.example\\FieldOps\\Fleet\\vehicles.csv", "Resolved"))
        self.assertEqual(self.source("Depot"), ("JSON file", "depots.json", "H:\\Shared\\depots.json", "Resolved"))

    def test_combine_files_from_a_folder(self):
        self.assertEqual(self.source("Timesheet"), ("Folder", "(all files)",
                                                    "\\\\fs01.corp.example\\FieldOps\\Timesheets\\", "Resolved"))

    def test_helper_queries_add_no_rows_and_nothing_is_unresolved(self):
        rows = self.payload["primarySources"]["rows"]
        self.assertEqual(len(rows), 10)
        self.assertFalse(self.payload["primarySources"]["unresolved"])
        self.assertEqual({r["status"] for r in rows}, {"Resolved"})


if __name__ == "__main__":
    unittest.main()
