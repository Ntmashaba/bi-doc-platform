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
    "tables": [{"name": "Sales", "isHidden": False, "columns": [{"name": "Amount", "dataType": "double", "isHidden": False}]}],
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

    def test_unusable_pbi_tools_is_reported_as_could_not_run(self):
        with tempfile.TemporaryDirectory() as d:
            code = ce.main([str(Path(d) / "x.pbix"), "--pbi-tools", str(Path(d) / "missing.exe"), "--report", str(Path(d) / "r.json")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
