"""The B16 evidence script: extract/record/report with the pbi-tools stand-in (runs on Windows CI too)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
sys.path.insert(0, str(ROOT / "packaging" / "windows" / "b16"))
from fixtures import pbi_model  # noqa: E402

import b16_gate  # noqa: E402

FAKE = [sys.executable, str(HERE / "fake_pbi_tools.py")]


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["FAKE_PBI_MODEL"] = str(pbi_model(self.tmp / "model"))
        os.environ["FAKE_PBI_MODE"] = "ok"
        self.pbix = self.tmp / "Sample.pbix"
        self.pbix.write_bytes(b"PK stand-in")
        self.results = self.tmp / "results.jsonl"

    def extract(self, scenario):
        return b16_gate.main(["extract", "--scenario", scenario, "--pbix", str(self.pbix), "--results",
                              str(self.results), "--tool-command", *FAKE])

    def test_records_evidence_without_report_content(self):
        self.assertEqual(self.extract("interactive"), 0)
        entry = json.loads(self.results.read_text().splitlines()[0])
        self.assertEqual(entry["result"]["status"], "passed")
        self.assertGreater(entry["result"]["objects"]["table"], 0)
        self.assertIn("account", entry["environment"])
        if os.name == "nt":                                                  # the session probes work on Windows
            env = entry["environment"]
            self.assertIn("session_id", env)
            self.assertIsInstance(env["input_desktop_available"], bool)
            self.assertGreater(env["uptime_minutes"], 0)
        self.assertNotIn("Revenue", self.results.read_text())                # no measure names, no content
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith("b16-")], [])   # workspace removed

    def test_failures_are_recorded_and_the_report_is_honest(self):
        self.extract("interactive")
        self.extract("interactive")
        os.environ["FAKE_PBI_MODE"] = "fail"
        self.assertEqual(self.extract("locked"), 1)
        text = b16_gate.report(self.results)
        self.assertIn("| interactive: Signed in at the console, session unlocked (baseline). | 2 | 2 | **supported**", text)
        self.assertIn("**not supported**", text)                               # locked failed
        self.assertIn("| after_reboot:", text)
        self.assertIn("**not verified**", text)
        self.assertIn("Only signed-in, unlocked extraction is verified.", text)

    def test_cli_runs_as_a_script(self):
        out = subprocess.run([sys.executable, str(ROOT / "packaging" / "windows" / "b16" / "b16_gate.py"), "env"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("generator", json.loads(out.stdout))


if __name__ == "__main__":
    unittest.main()
