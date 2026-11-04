"""A batch item paired with a model: checked before anything is extracted, and its model's extract never kept.

The pairing comes from the report's own details file. It is resolved and checked first, so a bad one fails that item
without extracting anything. An ABF or PBIX model is extracted into the item's model workspace, which is released when
the item succeeds, fails or is cancelled.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "packages" / "engines" / "tests"))

from fixtures import adf_factory, pbi_model  # noqa: E402
from test_model_pairing import report  # noqa: E402
from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_generator import batch  # noqa: E402
from bidoc_generator.batch import Options, Runner  # noqa: E402
from bidoc_generator.extract import ExtractionCancelled, ExtractionError  # noqa: E402
from bidoc_generator.history import History  # noqa: E402


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.model = pbi_model(self.tmp / "Models" / "Sales")
        (self.tmp / "Models" / "Sales.pbix").write_bytes(b"PK model")
        self.report = report(self.tmp / "Reports" / "Sales.Report")
        self.report.with_name("Sales.Report.bidoc-details.json").write_text(
            json.dumps({"model": {"path": "../Models/Sales.pbix"}}), encoding="utf-8")
        self.runner = Runner(History(self.tmp / "home"))
        self.addCleanup(self.runner.shutdown)
        self.calls = []

    def fake_extract(self, source, workspace, tool, **kw):
        """Stands in for pbixray: the model's extract is a database.json beside the workspace's Model folder."""
        self.calls.append(Path(source).name)
        target = Path(workspace) / "Model"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.model, target / "database.json")
        return Path(workspace)

    def run_batch(self, *inputs):
        batch_id = self.runner.submit([str(i) for i in inputs], Options(output_dir=str(self.tmp / "out")))
        self.runner.wait()
        return {Path(i["source"]).name: i for i in self.runner.history.batch(batch_id)["items"]}

    def workspaces(self):
        root = self.runner.history.workspaces
        return sorted(p.name for p in root.iterdir()) if root.exists() else []

    def assert_no_model_workspace(self):
        self.assertEqual([w for w in self.workspaces() if w.endswith("-model")], [])

    def test_success_records_the_pairing_and_keeps_no_extract(self):
        with mock.patch.object(batch, "extract_pbix", side_effect=self.fake_extract), \
             mock.patch.object(batch, "check_tool", return_value="pbixray"):
            item = self.run_batch(self.report)["Sales.Report"]
        self.assertEqual(item["state"], "completed", item["errors"])
        data = validate_artifact(Path(item["artifact_path"]).read_bytes())["native_payload"]["data"]
        self.assertEqual(data["modelPairing"], {"model": "Sales.pbix", "selectedBy": "Sales.Report.bidoc-details.json",
                                                "verified": False})
        self.assertEqual(self.calls, ["Sales.pbix"])
        self.assertEqual(self.workspaces(), [])

    def test_a_bad_pairing_fails_before_anything_is_extracted_and_alone(self):
        source = self.tmp / "Reports" / "Thin.pbix"
        source.write_bytes(b"PK thin report")
        source.with_name("Thin.pbix.bidoc-details.json").write_text('{"model": "not an object"}', encoding="utf-8")
        factory = adf_factory(self.tmp / "factory")
        with mock.patch.object(batch, "extract_pbix", side_effect=self.fake_extract), \
             mock.patch.object(batch, "check_tool", return_value="pbixray"):
            items = self.run_batch(source, factory)
        self.assertEqual(items["Thin.pbix"]["state"], "failed")
        self.assertIn('Thin.pbix.bidoc-details.json: "model" must be', items["Thin.pbix"]["errors"][0]["message"])
        self.assertEqual(self.calls, [], "neither the report nor a model was extracted")
        self.assertEqual(items["factory"]["state"], "completed")
        self.assert_no_model_workspace()

    def test_a_model_that_cannot_be_extracted_fails_safely_and_keeps_nothing(self):
        failure = ExtractionError("EXTRACTION_FAILED", "pbixray could not read /home/someone/SEEDPATH/Sales.pbix: Password=x")
        with mock.patch.object(batch, "extract_pbix", side_effect=failure), \
             mock.patch.object(batch, "check_tool", return_value="pbixray"):
            item = self.run_batch(self.report)["Sales.Report"]
        self.assertEqual(item["state"], "failed")
        message = item["errors"][0]["message"]
        self.assertEqual(message, "The model Sales.pbix named in Sales.Report.bidoc-details.json could not be extracted "
                                  "(EXTRACTION_FAILED). Check it opens in Power BI Desktop or SSMS, or name another model.")
        self.assertNotIn("SEEDPATH", json.dumps(item))
        self.assert_no_model_workspace()

    def test_a_failure_after_the_model_is_extracted_keeps_nothing(self):
        with mock.patch.object(batch, "extract_pbix", side_effect=self.fake_extract), \
             mock.patch.object(batch, "check_tool", return_value="pbixray"), \
             mock.patch("bidoc_engines.generate.generate", side_effect=RuntimeError("generation broke")):
            item = self.run_batch(self.report)["Sales.Report"]
        self.assertEqual(item["state"], "failed")
        self.assertEqual(self.calls, ["Sales.pbix"])
        self.assert_no_model_workspace()

    def test_cancelling_while_the_model_is_extracted_keeps_nothing(self):
        with mock.patch.object(batch, "extract_pbix", side_effect=ExtractionCancelled()), \
             mock.patch.object(batch, "check_tool", return_value="pbixray"):
            item = self.run_batch(self.report)["Sales.Report"]
        self.assertEqual(item["state"], "cancelled")
        self.assert_no_model_workspace()

    def test_retry_pairs_again_after_the_file_is_fixed(self):
        self.report.with_name("Sales.Report.bidoc-details.json").write_text(
            json.dumps({"model": {"path": "../Models/Missing.pbix"}}), encoding="utf-8")
        with mock.patch.object(batch, "extract_pbix", side_effect=self.fake_extract), \
             mock.patch.object(batch, "check_tool", return_value="pbixray"):
            item = self.run_batch(self.report)["Sales.Report"]
            self.assertEqual(item["state"], "failed")
            self.assertEqual(self.calls, [])
            self.report.with_name("Sales.Report.bidoc-details.json").write_text(
                json.dumps({"model": {"path": "../Models/Sales.pbix"}}), encoding="utf-8")
            self.runner.retry(item["item_id"])
            self.runner.wait()
            again = self.runner.history.item(item["item_id"])
        self.assertEqual(again["state"], "completed", again["errors"])
        self.assertEqual(self.calls, ["Sales.pbix"])
        self.assertEqual(self.workspaces(), [])


if __name__ == "__main__":
    unittest.main()
