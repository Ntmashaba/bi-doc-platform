"""The extractor comparison used in the Windows validation pack: identical extracts match, a real difference is reported."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import compare_extractors as ce  # noqa: E402

PAYLOAD = {"model": {
    "tables": [{"name": "Sales", "isHidden": False, "columns": [{"name": "Amount", "dataType": "double", "isHidden": False}],
                "partitions": [{"name": "Sales-1", "mode": "import", "type": "m", "expression": "let Source = Sql.Database(\"s\", \"d\") in Source"}]}],
    "measures": [{"table": "Sales", "name": "Total", "expression": "SUM ( Sales[Amount] )", "formatString": "0"}],
    "relationships": [{"fromTable": "Sales", "fromColumn": "K", "toTable": "Date", "toColumn": "K", "isActive": True}],
    "roles": [{"name": "Reader"}], "expressions": []}}


class CompareExtractors(unittest.TestCase):
    def test_identical_extracts_have_no_differences(self):
        self.assertEqual(ce.differences(ce.compare(ce.facts(PAYLOAD), ce.facts(copy.deepcopy(PAYLOAD)))), 0)

    def test_missing_changed_and_whitespace_only_differences_are_told_apart(self):
        other = copy.deepcopy(PAYLOAD)
        other["model"]["measures"][0]["expression"] = "SUM(Sales[Amount])"
        other["model"]["tables"][0]["columns"].append({"name": "Extra", "dataType": "int64"})
        other["model"]["relationships"].clear()
        report = ce.compare(ce.facts(PAYLOAD), ce.facts(other))
        self.assertEqual(report["columns"]["only_in_pbixray"], ["Sales[Extra]"])
        self.assertEqual(len(report["relationships"]["only_in_pbi_tools"]), 1)
        changed = report["measures"]["changed"]["Sales[Total]"]
        self.assertFalse(changed["whitespace_only"])        # "SUM(" vs "SUM (" differs after squashing runs of spaces
        spaced = copy.deepcopy(PAYLOAD)
        spaced["model"]["measures"][0]["expression"] = "SUM (  Sales[Amount] )"
        self.assertTrue(ce.compare(ce.facts(PAYLOAD), ce.facts(spaced))["measures"]["changed"]["Sales[Total]"]["whitespace_only"])
        self.assertEqual(ce.differences(report), 3)

    def test_partition_storage_mode_missing_partitions_and_definitions_are_compared(self):
        mode = copy.deepcopy(PAYLOAD)
        mode["model"]["tables"][0]["partitions"][0]["mode"] = "directQuery"           # Import vs DirectQuery
        report = ce.compare(ce.facts(PAYLOAD), ce.facts(mode))
        changed = report["partitions"]["changed"]["Sales/Sales-1"]
        self.assertEqual((changed["pbi_tools"]["mode"], changed["pbixray"]["mode"]), ("import", "directQuery"))
        self.assertEqual(ce.differences(report), 1)

        definition = copy.deepcopy(PAYLOAD)
        definition["model"]["tables"][0]["partitions"][0]["expression"] = "let Source = Excel.Workbook() in Source"
        self.assertEqual(list(ce.compare(ce.facts(PAYLOAD), ce.facts(definition))["partitions"]["changed"]), ["Sales/Sales-1"])

        missing = copy.deepcopy(PAYLOAD)
        missing["model"]["tables"][0]["partitions"] = []
        report = ce.compare(ce.facts(PAYLOAD), ce.facts(missing))
        self.assertEqual(report["partitions"]["only_in_pbi_tools"], ["Sales/Sales-1"])
        self.assertEqual(ce.compare(ce.facts(missing), ce.facts(PAYLOAD))["partitions"]["only_in_pbixray"], ["Sales/Sales-1"])

    def run_main(self, report_path):
        from unittest import mock
        fake = mock.Mock(available=True, reason=None)
        with mock.patch("bidoc_generator.backend.select_backend", return_value=fake), \
                mock.patch.object(ce, "extract_and_load", return_value=PAYLOAD):
            return ce.main(["x.pbix", "--pbi-tools", "t.exe", "--report", str(report_path)])

    def test_report_parent_directory_is_created(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "compare" / "nested" / "r.json"
            self.assertEqual(self.run_main(path), 0)
            self.assertTrue(path.is_file())

    def test_a_report_that_cannot_be_written_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "file"
            blocker.write_text("not a directory")
            self.assertEqual(self.run_main(blocker / "r.json"), 2)         # parent is a file
            self.assertEqual(self.run_main(Path(d)), 2)                    # the report path is a directory

    def test_unusable_pbi_tools_is_reported_as_could_not_run(self):
        with tempfile.TemporaryDirectory() as d:
            code = ce.main([str(Path(d) / "x.pbix"), "--pbi-tools", str(Path(d) / "missing.exe"), "--report", str(Path(d) / "r.json")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
