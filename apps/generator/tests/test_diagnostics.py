"""pbi-tools failures keep their cause, name the phase, and the diagnostic report carries no names."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bidoc_generator.diagnostics import cause_lines, exit_code_info, failure_message, redact  # noqa: E402
from test_backend_consistency import pid_alive  # noqa: E402
from bidoc_generator.extract import ExtractionError, extract_pbix, python_tool  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "fake_pbi_tools.py")
TRACE = "".join(f"   at Frame.Number{i}.Method{i}(Object sender, EventArgs e)\n" for i in range(60))
LOG = ("ERROR: The file is password protected.\nUnhandled exception. System.Exception: boom\n" + TRACE)


class Pure(unittest.TestCase):
    def test_exit_code_is_shown_signed_and_hex(self):
        self.assertEqual(exit_code_info(4294967287), {"unsigned": 4294967287, "signed": -9, "hex": "0xFFFFFFF7"})
        self.assertEqual(exit_code_info(-9)["unsigned"], 4294967287)
        self.assertEqual(exit_code_info(3), {"unsigned": 3, "signed": 3, "hex": "0x00000003"})

    def test_cause_is_found_before_the_trailing_stack_trace(self):
        self.assertGreater(len(TRACE), 600)                                 # the old 600-character tail is all frames
        self.assertNotIn("password", LOG[-600:])
        self.assertEqual(cause_lines(LOG)[0], "ERROR: The file is password protected.")
        msg = failure_message(4294967287, LOG, "pbi-tools.log")
        self.assertIn("password protected", msg)
        self.assertIn("-9 (0xFFFFFFF7)", msg)
        self.assertIn("started but the extraction failed", msg)
        self.assertNotIn("Frame.Number", msg)

    def test_output_without_a_named_cause_says_so(self):
        self.assertIn("no output that names a cause", failure_message(1, "", "pbi-tools.log"))

    def test_redaction_removes_names_paths_and_connection_strings(self):
        text = ("Cannot open D:\\Reports\\Quarterly Sales.pbix at /home/ann/Quarterly Sales.pbix; "
                "Data Source=srv-prod01;Initial Catalog=SalesDb;Password=hunter2; https://x.example/api?k=1 "
                "ann@corp.com 3629281d-52fb-43d7-800f-d4e976dd3ede \\\\fileserver\\share\\a.pbix Contoso")
        out = redact(text, names=["Quarterly Sales", "Contoso"])
        for leak in ("Reports", "Quarterly", "srv-prod01", "SalesDb", "hunter2", "example", "corp.com", "3629281d",
                     "fileserver", "Contoso", "/home/ann"):
            self.assertNotIn(leak, out)
        self.assertIn("Cannot open", out)


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.addCleanup(os.environ.pop, "FAKE_PBI_MODE", None)
        self.pbix = self.tmp / "Secret.pbix"
        self.pbix.write_bytes(b"x")

    def test_extraction_failure_reports_the_early_cause_and_phase(self):
        os.environ["FAKE_PBI_MODE"] = "crash"
        with self.assertRaises(ExtractionError) as ctx:
            extract_pbix(self.pbix, self.tmp / "ws", FAKE, command=python_tool(FAKE), timeout=30)
        self.assertEqual(ctx.exception.code, "EXTRACTION_FAILED")
        msg = str(ctx.exception)
        self.assertIn("started but the extraction failed", msg)
        self.assertIn("Could not load file or assembly", msg)             # the cause, not the last trace lines
        self.assertNotIn("Frame.Number39", msg)
        self.assertTrue((self.tmp / "ws" / "pbi-tools.log").is_file())      # the full output stays

    def test_launch_failure_is_a_prerequisite_not_an_extraction_failure(self):
        with self.assertRaises(ExtractionError) as ctx:
            extract_pbix(self.pbix, self.tmp / "ws2", str(self.tmp / "missing.exe"), timeout=5)
        self.assertEqual(ctx.exception.code, "PREREQUISITE_MISSING")

    def diag(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
        import diagnose_extractors as d
        return d

    def test_diagnose_script_report_has_no_names(self):
        d = self.diag()
        os.environ["FAKE_PBI_MODE"] = "crash"
        report = self.tmp / "out" / "r.json"
        code = d.main([str(self.pbix), "--pbi-tools", "unused.exe", "--launcher", FAKE, "--report", str(report),
                       "--redact-also", "Secret"])
        self.assertEqual(code, 1)
        text = report.read_text()
        for leak in ("Secret", str(self.tmp), "D:\\\\Reports"):
            self.assertNotIn(leak, text)
        data = json.loads(text)
        runs = {r["name"]: r for r in data["runs"]}
        self.assertEqual(set(runs), {"platform", "standalone", "pbixray"} | ({"short-path"} if "short-path" in runs
                                                                              else {"renamed-copy"}))
        self.assertEqual(runs["platform"]["phase"], "extraction")
        self.assertTrue(any("Could not load file or assembly" in x for x in runs["platform"]["first_problems"]))
        self.assertEqual(data["pbix"]["size"], "<1 MiB")
        self.assertIn("best-effort", data["purpose"])
        copy = runs.get("short-path") or runs.get("renamed-copy")
        self.assertEqual(set(copy["path_chars"]), {"original", "copy"})

    def hanging_tree(self):
        os.environ["FAKE_PBI_MODE"] = "tree"
        pidfile = self.tmp / "grandchild.pid"
        os.environ["FAKE_PBI_PIDFILE"] = str(pidfile)
        self.addCleanup(os.environ.pop, "FAKE_PBI_PIDFILE", None)
        return pidfile

    def grandchild(self, pidfile):
        for _ in range(200):
            if pidfile.exists() and pidfile.read_text():
                return int(pidfile.read_text())
            time.sleep(0.05)
        self.fail("the fake tool never started its child")

    def gone(self, pid):
        for _ in range(100):
            if not pid_alive(pid):
                return True
            time.sleep(0.05)
        return False

    def build(self, p, target):
        return [sys.executable, FAKE, "extract", str(p), "-extractFolder", str(target), "-modelSerialization", "Raw"]

    def test_timeout_ends_the_whole_process_tree(self):
        d = self.diag()
        pidfile = self.hanging_tree()
        result = d.run_variant("platform", self.build, self.pbix, [], 3, inherit_stdin=False, new_group=True)
        self.assertEqual(result["phase"], "timeout")
        self.assertTrue(self.gone(self.grandchild(pidfile)), "the grandchild survived the timeout")

    def test_interruption_ends_the_whole_process_tree_and_propagates(self):
        d = self.diag()
        pidfile = self.hanging_tree()
        real, calls = subprocess.Popen.wait, []

        def interrupted(proc, timeout=None):
            calls.append(1)
            if len(calls) == 1:                    # the first wait is the one inside _run: the user pressed Ctrl+C
                self.grandchild(pidfile)
                raise KeyboardInterrupt
            return real(proc, timeout=timeout)
        with mock.patch.object(subprocess.Popen, "wait", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                d.run_variant("platform", self.build, self.pbix, [], 60, inherit_stdin=False, new_group=True)
        self.assertTrue(self.gone(self.grandchild(pidfile)), "the grandchild survived the interruption")


if __name__ == "__main__":
    unittest.main()
