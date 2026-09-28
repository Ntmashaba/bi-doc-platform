"""bidoc command line: exit codes, JSON output and doctor diagnostics."""
import contextlib
import io
import json
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
        self.assertIn("B08", report["inputs"]["pbix"]["reason"])


if __name__ == "__main__":
    unittest.main()
