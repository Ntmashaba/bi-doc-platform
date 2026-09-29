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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from bidoc_generator.cli import main  # noqa: E402

# Public Microsoft Learning lab file (DP-500 lab 08: DirectQuery + Import, server/database as parameters).
REAL_SAMPLE = Path(__file__).resolve().parent / "fixtures" / "dp500-08-composite.pbix"


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

    def test_config_keeps_the_pbi_tools_path(self):
        os.environ["BIDOC_HOME"] = str(self.tmp / "home")
        self.addCleanup(os.environ.pop, "BIDOC_HOME", None)
        tool = self.tmp / "pbi-tools.exe"
        tool.write_bytes(b"")
        self.assertEqual(run(["config", "--pbi-tools", str(self.tmp / "nope.exe")])[0], 2)
        self.assertEqual(run(["config", "--pbi-tools", str(tool)])[0], 0)
        report = json.loads(run(["doctor", "--json"])[1])
        self.assertEqual(report["pbi_tools"], str(tool.resolve()))
        run(["config", "--pbi-tools", ""])
        self.assertEqual(json.loads(run(["config", "--json"])[1]), {})

    def test_pbix_without_prerequisites_exits_3(self):                   # A07
        pbix = self.tmp / "r.pbix"
        pbix.write_bytes(b"PK")
        code, _, err = run(["generate", "--engine", "power_bi", "--source", str(pbix), "--kind", "pbix",
                            "--output-dir", str(self.tmp / "out"), "--pbi-tools", str(self.tmp / "missing.exe")])
        self.assertEqual(code, 3)
        self.assertIn("PBIP, model and ADF inputs still work", err)

    def test_pbixray_and_pbi_tools_are_alternatives(self):
        pbix = self.tmp / "r.pbix"
        pbix.write_bytes(b"PK")
        code, _, err = run(["generate", "--engine", "power_bi", "--source", str(pbix), "--kind", "pbix",
                            "--output-dir", str(self.tmp / "out"), "--pbixray", "--pbi-tools", "x.exe"])
        self.assertEqual(code, 2)
        self.assertIn("alternatives", err)

    def test_real_pbix_with_the_pbixray_extractor(self):
        # A real DirectQuery/composite PBIX end to end, extracted in a child process without pbi-tools.
        from bidoc_contracts import validate_artifact  # noqa: PLC0415
        os.environ["BIDOC_HOME"] = str(self.tmp / "home")
        self.addCleanup(os.environ.pop, "BIDOC_HOME", None)
        # Generation writes an identity sidecar beside the source, so work on a copy, never the fixture.
        source = self.tmp / REAL_SAMPLE.name
        shutil.copy(REAL_SAMPLE, source)
        code, out, err = run(["generate", "--engine", "power_bi", "--source", str(source), "--kind", "pbix",
                              "--output-dir", str(self.tmp / "out"), "--profile", "shared", "--pbixray", "--json"])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        manifest = validate_artifact(Path(result["artifact_path"]).read_bytes(),
                                     view_ids=["pbi.overview", "pbi.table", "pbi.measure", "pbi.source", "pbi.page"])
        kinds = [o["kind"] for o in manifest["objects"]]
        self.assertEqual((kinds.count("table"), kinds.count("measure")), (6, 2))
        servers = {(o["bindings"][0]["endpoint"]["system"], o["bindings"][0]["endpoint"]["server"])
                   for o in manifest["objects"] if o["kind"] == "source" and o["bindings"][0]["endpoint"]["server"]}
        self.assertEqual(servers, {("SQL Server", "localhost")})

    def test_a_different_pbixray_version_is_named_by_doctor_and_the_portable_command(self):
        from unittest import mock  # noqa: PLC0415
        from bidoc_generator.doctor import diagnose  # noqa: PLC0415
        from bidoc_generator.extract import ExtractionError, check_tool, pbixray_command  # noqa: PLC0415
        with mock.patch("importlib.metadata.version", return_value="0.16.0"):
            report = diagnose("pbixray")
            check = next(c for c in report["checks"] if c["check"] == "pbixray")
            self.assertEqual((check["ok"], check["detail"]), (False, "0.16.0"))     # installed, not "not installed"
            self.assertIn("validated only for", check["fix"])
            self.assertFalse(report["inputs"]["pbix"]["available"])
            for call in (pbixray_command, lambda: check_tool("pbixray")):
                with self.assertRaisesRegex(ExtractionError, "validated only for"):
                    call()

    def test_the_portable_reader_is_opt_in(self):
        from bidoc_generator.doctor import diagnose  # noqa: PLC0415
        with mock.patch("bidoc_generator.doctor._pbi_tools", return_value=None):
            self.assertNotEqual(diagnose()["pbi_tools"], "pbixray")           # not chosen unless asked for
            self.assertEqual(diagnose("pbixray")["pbi_tools"], "pbixray")

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
