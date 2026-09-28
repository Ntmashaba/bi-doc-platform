"""bidoc command line: exit codes, JSON output and doctor diagnostics."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from bidoc_generator.cli import main  # noqa: E402


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:          # argparse errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.factory = adf_factory(self.tmp / "factory")

    def test_generate_json(self):
        code, out, _ = run(["generate", "--engine", "adf", "--source", str(self.factory), "--kind", "adf_git",
                            "--output-dir", str(self.tmp / "out"), "--profile", "shared", "--json"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(Path(result["artifact_path"]).is_file())

    def test_invalid_input_exits_2(self):
        code, _, err = run(["generate", "--engine", "adf", "--source", str(self.tmp / "nope"), "--kind", "adf_git",
                            "--output-dir", str(self.tmp / "out")])
        self.assertEqual(code, 2)
        self.assertIn("input not found", err)
        self.assertEqual(run(["generate", "--engine", "adf", "--source", "x", "--kind", "pbix",
                              "--output-dir", "o"])[0], 2)

    def test_incomplete_shared_exits_4_local_exits_0(self):
        (self.factory / "pipeline" / "broken.json").write_text("{", encoding="utf-8")
        base = ["generate", "--engine", "adf", "--source", str(self.factory), "--kind", "adf_git",
                "--output-dir", str(self.tmp / "out")]
        self.assertEqual(run(base + ["--profile", "shared"])[0], 4)
        self.assertEqual(run(base)[0], 0)

    def test_doctor(self):
        code, out, _ = run(["doctor", "--json", "--pbi-tools", str(self.tmp / "missing.exe")])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["inputs"]["pbip"]["available"])
        self.assertTrue(report["inputs"]["adf_git"]["available"])
        self.assertFalse(report["inputs"]["pbix"]["available"])          # PBIX never blocks PBIP/ADF (A07)
        self.assertIn("pbi-tools not found", report["inputs"]["pbix"]["reason"])

    def test_pbix_without_prerequisites_exits_3(self):                   # A07
        pbix = self.tmp / "r.pbix"
        pbix.write_bytes(b"PK")
        code, _, err = run(["generate", "--engine", "power_bi", "--source", str(pbix), "--kind", "pbix",
                            "--output-dir", str(self.tmp / "out"), "--pbi-tools", str(self.tmp / "missing.exe")])
        self.assertEqual(code, 3)
        self.assertIn("PBIP, model and ADF inputs still work", err)

    def test_batch_partial_failure_exits_5_and_history_lists_it(self):  # A08
        home = self.tmp / "home"
        os.environ["BIDOC_HOME"] = str(home)
        self.addCleanup(os.environ.pop, "BIDOC_HOME", None)
        other = adf_factory(self.tmp / "other")
        code, out, _ = run(["batch", str(self.factory), str(self.tmp / "missing"), str(other),
                            "--output-dir", str(self.tmp / "out"), "--json"])
        self.assertEqual(code, 5)
        batch = json.loads(out)
        self.assertEqual([i["state"] for i in batch["items"]], ["completed", "failed", "completed"])
        code, out, _ = run(["history", "--json"])
        self.assertEqual(json.loads(out)[0]["batch_id"], batch["batch_id"])
        failed = batch["items"][1]["item_id"]
        adf_factory(self.tmp / "missing")
        code, out, _ = run(["retry", failed, "--json"])
        self.assertEqual((code, json.loads(out)["state"]), (0, "completed"))
        self.assertEqual(run(["retry", failed])[0], 2)                   # completed items are not retried
        self.assertEqual(run(["batch", str(self.tmp / "nope"), "--output-dir", str(self.tmp / "o")])[0], 4)


if __name__ == "__main__":
    unittest.main()
