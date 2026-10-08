"""B08: batch queue, PBIX extraction in a child process, cancellation, history (A07, A08, A17)."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "packages" / "engines" / "tests"))
from fixtures import adf_factory, pbi_model  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_generator.batch import Options, Runner, classify  # noqa: E402
from bidoc_generator.extract import python_tool  # noqa: E402
from bidoc_generator.history import History  # noqa: E402

FAKE = python_tool(str(HERE / "fake_pbi_tools.py"))


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                   # a zombie is not running
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "out"
        self.model = pbi_model(self.tmp / "model")                # model.bim
        os.environ["FAKE_PBI_MODEL"] = str(self.model)
        os.environ["FAKE_PBI_MODE"] = "ok"
        self.pbix = self.tmp / "Sales Report.pbix"
        self.pbix.write_bytes(b"PK fake pbix")

    def runner(self, **kw):
        kw.setdefault("tool_command", FAKE)
        r = Runner(History(self.tmp / "home"), **kw)
        self.addCleanup(r.shutdown)
        return r

    def items(self, runner, batch_id):
        self.assertTrue(runner.wait(60))
        return runner.history.batch(batch_id)["items"]

    def test_a08_one_bad_item_does_not_stop_the_others_and_can_be_retried(self):
        r = self.runner()
        missing = self.tmp / "later-factory"
        batch = r.submit([str(adf_factory(self.tmp / "factory")), str(missing), str(self.model)],
                         Options(output_dir=str(self.out), profile="shared"))
        items = self.items(r, batch)
        self.assertEqual([i["state"] for i in items], ["completed", "failed", "completed"])
        for it in (items[0], items[2]):
            validate_artifact(Path(it["artifact_path"]).read_bytes())
        self.assertEqual(items[1]["errors"][0]["code"], "INVALID_INPUT")
        # a batch document says which bidoc generated it, like one from `bidoc generate`
        from bidoc_generator import __version__
        produced = validate_artifact(Path(items[2]["artifact_path"]).read_bytes())["native_payload"]["data"]["producer"]
        self.assertEqual((produced["engine"], produced["bidoc"]), ("pbi-doc-gen", __version__))
        adf_factory(missing)                                      # fix the input, then retry that item only
        with self.assertRaises(ValueError):
            r.retry(items[0]["item_id"])                          # completed items are not retried
        r.retry(items[1]["item_id"])
        items = self.items(r, batch)
        self.assertEqual([i["state"] for i in items], ["completed", "completed", "completed"])

    def test_classify_needs_complete_projects(self):
        (self.tmp / "p").mkdir()
        (self.tmp / "p" / "Sales.pbip").write_text("{}")
        self.assertIn("pointer", classify(self.tmp / "p" / "Sales.pbip")["errors"][0]["message"])
        self.assertIn("no .SemanticModel", classify(self.tmp / "p")["errors"][0]["message"])
        (self.tmp / "p" / "Sales.SemanticModel").mkdir()
        self.assertEqual(classify(self.tmp / "p" / "Sales.pbip")["kind"], "pbip")
        self.assertEqual(classify(self.tmp / "p")["kind"], "pbip")
        self.assertEqual(classify(self.pbix)["kind"], "pbix")
        self.assertEqual(classify(self.model)["kind"], "bim")
        script = self.tmp / "Sales create.xmla"                      # an SSMS CREATE script of a tabular database
        script.write_text('{"create": {"database": {"name": "Sales", "compatibilityLevel": 1500, "model": {"tables": []}}}}')
        self.assertEqual((classify(script)["engine"], classify(script)["kind"]), ("power_bi", "bim"))
        self.assertEqual(classify(adf_factory(self.tmp / "f"))["kind"], "adf_git")

    def test_pbix_is_extracted_in_its_own_workspace(self):
        r = self.runner()
        items = self.items(r, r.submit([str(self.pbix)], Options(output_dir=str(self.out), profile="shared")))
        self.assertEqual(items[0]["state"], "completed", items[0]["errors"])
        m = validate_artifact(Path(items[0]["artifact_path"]).read_bytes())
        self.assertEqual((m["source"]["kind"], m["source"]["label"], m["title"]),
                         ("pbix", "Sales Report.pbix", "Sales Report"))
        self.assertEqual(len(m["source"]["sha256"]), 64)
        self.assertFalse((r.history.workspaces / items[0]["item_id"]).exists())   # cleaned after success
        self.assertFalse((self.tmp / "Sales Report").exists())                   # nothing beside the PBIX

    def test_a07_pbix_unavailable_other_inputs_still_work(self):
        r = self.runner(pbix_ready="pbi-tools not found")
        items = self.items(r, r.submit([str(self.pbix), str(self.model)], Options(output_dir=str(self.out))))
        self.assertEqual([i["state"] for i in items], ["failed", "completed"])
        self.assertEqual(items[0]["errors"][0], {"code": "PREREQUISITE_MISSING", "message": "pbi-tools not found"})

    def test_extraction_failure_keeps_its_log(self):
        os.environ["FAKE_PBI_MODE"] = "fail"
        r = self.runner()
        item = self.items(r, r.submit([str(self.pbix)], Options(output_dir=str(self.out))))[0]
        self.assertEqual((item["state"], item["errors"][0]["code"]), ("failed", "EXTRACTION_FAILED"))
        self.assertIn("could not open", item["errors"][0]["message"])
        self.assertTrue((r.history.workspaces / item["item_id"] / "pbi-tools.log").is_file())

    def test_a17_cancel_kills_the_process_tree_and_the_next_item_runs(self):
        os.environ["FAKE_PBI_MODE"] = "tree"
        pidfile = self.tmp / "grandchild.pid"
        os.environ["FAKE_PBI_PIDFILE"] = str(pidfile)
        r = self.runner()
        batch = r.submit([str(self.pbix), str(self.model)], Options(output_dir=str(self.out)))
        for _ in range(200):
            if pidfile.exists() and pidfile.read_text():
                break
            time.sleep(0.05)
        grandchild = int(pidfile.read_text())
        self.assertTrue(pid_alive(grandchild))
        first = r.history.batch(batch)["items"][0]
        self.assertEqual(first["state"], "extracting")
        self.assertTrue(r.cancel(first["item_id"]))
        items = self.items(r, batch)
        self.assertEqual([i["state"] for i in items], ["cancelled", "completed"])
        for _ in range(50):
            if not pid_alive(grandchild):
                break
            time.sleep(0.05)
        self.assertFalse(pid_alive(grandchild), "the grandchild survived cancellation")

    def test_timeout_fails_the_item(self):
        os.environ["FAKE_PBI_MODE"] = "hang"
        r = self.runner()
        item = self.items(r, r.submit([str(self.pbix)], Options(output_dir=str(self.out), extract_timeout=1)))[0]
        self.assertEqual((item["state"], item["errors"][0]["code"]), ("failed", "EXTRACTION_TIMEOUT"))

    def test_cancel_queued_item(self):
        os.environ["FAKE_PBI_MODE"] = "hang"
        r = self.runner()
        batch = r.submit([str(self.pbix), str(self.model)], Options(output_dir=str(self.out)))
        second = r.history.batch(batch)["items"][1]["item_id"]
        self.assertTrue(r.cancel(second))
        self.assertEqual(r.cancel_batch(batch), 1)
        self.assertEqual([i["state"] for i in self.items(r, batch)], ["cancelled", "cancelled"])

    def test_interrupted_work_is_marked_on_next_launch_and_retryable(self):
        home = self.tmp / "home"
        h = History(home)
        batch = h.create_batch(str(self.out), Options(output_dir=str(self.out)).__dict__,
                               [{"source": str(self.model), "label": "m", "engine": "power_bi", "kind": "bim"}])
        item = h.batch(batch)["items"][0]["item_id"]
        h.update(item, state="rendering")
        (h.workspaces / item).mkdir()
        h2 = History(home)                                     # "next launch"
        self.assertEqual(h2.interrupted, 1)
        self.assertEqual(h2.item(item)["state"], "interrupted")
        self.assertEqual(h2.item(item)["errors"][0]["code"], "INTERRUPTED")
        r = Runner(h2)
        self.addCleanup(r.shutdown)
        r.retry(item)
        self.assertTrue(r.wait(60))
        self.assertEqual(h2.item(item)["state"], "completed")
        with sqlite3.connect(home / "history.sqlite3") as conn:
            self.assertEqual(conn.execute("SELECT attempt FROM items").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
