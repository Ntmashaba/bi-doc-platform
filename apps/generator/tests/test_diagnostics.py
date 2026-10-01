"""pbi-tools failures keep their cause, name the phase, and the diagnostic report carries no names."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bidoc_generator.diagnostics import cause_lines, exit_code_info, failure_message, redact  # noqa: E402
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

    def test_diagnose_script_report_has_no_names(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
        import diagnose_extractors as d
        os.environ["FAKE_PBI_MODE"] = "crash"
        launcher = self.tmp / "tool.py"
        launcher.write_text(f'import sys, runpy\nsys.argv = ["{FAKE.replace(chr(92), "/")}"] + sys.argv[1:]\n'
                            f'runpy.run_path("{FAKE.replace(chr(92), "/")}", run_name="__main__")\n')
        if os.name == "nt":
            self.skipTest("the launcher shim is POSIX only")
        wrapper = self.tmp / "pbi-tools"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {launcher} \"$@\"\n")
        wrapper.chmod(0o755)
        report = self.tmp / "out" / "r.json"
        code = d.main([str(self.pbix), "--pbi-tools", str(wrapper), "--report", str(report), "--redact-also", "Secret"])
        self.assertEqual(code, 1)
        text = report.read_text()
        for leak in ("Secret", str(self.tmp), "D:\\\\Reports"):
            self.assertNotIn(leak, text)
        data = json.loads(text)
        names = {r["name"]: r for r in data["runs"]}
        self.assertEqual(set(names), {"platform", "standalone", "short-path", "pbixray"})
        self.assertEqual(names["platform"]["phase"], "extraction")
        self.assertTrue(any("Could not load file or assembly" in x for x in names["platform"]["first_problems"]))
        self.assertEqual(data["pbix"]["size"], "<1 MiB")


if __name__ == "__main__":
    unittest.main()
