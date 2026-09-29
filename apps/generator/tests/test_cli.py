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

    # --- which PBIX extractor `generate` uses -------------------------------------------------------------
    @staticmethod
    def _report(tool, available=True, reason=None):
        return {"pbi_tools": tool, "inputs": {"pbix": {"available": available, "reason": reason}}}

    def _select(self, backend, pbi_tools, reports):
        """select_report with a scripted diagnose; returns (report, fell_back, arguments diagnose was asked with)."""
        from bidoc_generator.cli import select_report  # noqa: PLC0415
        asked = []

        def fake(arg):
            asked.append(arg)
            return reports[arg]
        report, fell_back = select_report(backend, pbi_tools, fake)
        return report, fell_back, asked

    def test_default_backend_uses_a_configured_pbi_tools(self):
        # diagnose(None) is what finds BIDOC_PBI_TOOLS / config.json / PATH; the default must not bypass it.
        report, fell_back, asked = self._select("auto", None, {None: self._report("C:/tools/pbi-tools.exe")})
        self.assertEqual((report["pbi_tools"], fell_back, asked), ("C:/tools/pbi-tools.exe", False, [None]))

    def test_default_backend_falls_back_to_portable_only_when_none_is_configured(self):
        report, fell_back, asked = self._select("auto", None, {None: self._report("pbixray")})
        self.assertEqual((report["pbi_tools"], fell_back, asked), ("pbixray", False, [None]))

    def test_default_backend_falls_back_when_the_configured_pbi_tools_cannot_run(self):
        unusable = self._report("C:/tools/pbi-tools.exe", False, "PBIX extraction runs on Windows only")
        report, fell_back, asked = self._select("auto", None, {None: unusable, "pbixray": self._report("pbixray")})
        self.assertEqual((report["pbi_tools"], fell_back, asked), ("pbixray", True, [None, "pbixray"]))

    def test_an_explicit_choice_is_never_overridden(self):
        unusable = self._report(None, False, "pbi-tools not found")
        both = {"x.exe": unusable, "pbi-tools": unusable, None: unusable, "pbixray": self._report("pbixray")}
        # a mistyped --pbi-tools path stays an error
        report, fell_back, asked = self._select("auto", "x.exe", both)
        self.assertEqual((report["inputs"]["pbix"]["available"], fell_back, asked), (False, False, ["x.exe"]))
        # --backend pbi-tools stays an error rather than quietly using the portable reader
        report, fell_back, asked = self._select("pbi-tools", None, both)
        self.assertEqual((report["inputs"]["pbix"]["available"], fell_back, asked), (False, False, [None]))
        # --backend pbixray is portable whatever is configured
        report, fell_back, asked = self._select("pbixray", None, both)
        self.assertEqual((report["pbi_tools"], fell_back, asked), ("pbixray", False, ["pbixray"]))

    def test_generate_passes_the_configured_pbi_tools_to_extraction(self):
        # End to end through main(): the default backend hands the configured tool to the extractor.
        from unittest import mock  # noqa: PLC0415
        from bidoc_generator.extract import ExtractionError  # noqa: PLC0415
        os.environ["BIDOC_HOME"] = str(self.tmp / "home")
        self.addCleanup(os.environ.pop, "BIDOC_HOME", None)
        pbix = self.tmp / "r.pbix"
        pbix.write_bytes(b"PK")
        tool = self.tmp / "pbi-tools.exe"
        tool.write_bytes(b"")
        with mock.patch("bidoc_generator.cli.diagnose",
                              # only diagnose(None) finds the configured tool; asking for "pbixray" gets the portable reader
                              side_effect=lambda arg: self._report(str(tool)) if arg is None else self._report("pbixray")), \
                mock.patch("bidoc_generator.extract.extract_pbix",
                           side_effect=ExtractionError("EXTRACTION_FAILED", "stop here")) as extract:
            code, _, err = run(["generate", "--engine", "power_bi", "--source", str(pbix), "--kind", "pbix",
                                "--output-dir", str(self.tmp / "out")])
        self.assertEqual(extract.call_args.args[2], str(tool.resolve()))
        self.assertIn("extracting (pbi-tools)", err)
        self.assertNotEqual(code, 0)

    def test_doctor_uses_the_portable_reader_only_when_nothing_is_configured(self):
        from bidoc_generator.doctor import diagnose  # noqa: PLC0415
        with mock.patch("bidoc_generator.doctor._pbi_tools", return_value=None):
            self.assertEqual(diagnose()["pbi_tools"], "pbixray")               # nothing configured
        with mock.patch("bidoc_generator.doctor._pbi_tools", return_value="C:/tools/pbi-tools.exe"):
            self.assertEqual(diagnose()["pbi_tools"], "C:/tools/pbi-tools.exe")  # a configured tool is kept
            self.assertEqual(diagnose("pbixray")["pbi_tools"], "pbixray")         # unless asked for by name

    def test_batch_and_worker_extraction_use_the_portable_reader_only_when_nothing_is_configured(self):
        from bidoc_generator.extract import ExtractionError, check_tool  # noqa: PLC0415
        self.assertEqual(check_tool(None), "pbixray")
        with mock.patch("importlib.metadata.version", return_value="0.16.0"):
            with self.assertRaisesRegex(ExtractionError, "pbi-tools is not configured"):
                check_tool(None)

    # --- Tabular ABF input --------------------------------------------------------------------------------
    def test_an_abf_file_is_recognised_and_needs_the_portable_reader(self):
        from bidoc_generator.batch import classify  # noqa: PLC0415
        from bidoc_generator.doctor import diagnose  # noqa: PLC0415
        from bidoc_generator.extract import ExtractionError, extract_pbix  # noqa: PLC0415
        abf = self.tmp / "model.abf"
        abf.write_bytes(b"not a real backup")
        found = classify(abf)
        self.assertEqual((found["engine"], found["kind"]), ("power_bi", "abf"))
        with self.assertRaisesRegex(ExtractionError, "ABF requires the pbixray backend"):
            extract_pbix(abf, self.tmp / "ws", "C:/tools/pbi-tools.exe")
        with mock.patch("importlib.metadata.version", return_value="0.16.0"):
            state = diagnose()["inputs"]["abf"]
        self.assertFalse(state["available"])
        self.assertIn("validated only for", state["reason"])
        self.assertTrue(diagnose()["inputs"]["abf"]["available"])       # the installed pbixray is in range

    def test_abf_generation_cannot_be_combined_with_pbi_tools(self):
        abf = self.tmp / "model.abf"
        abf.write_bytes(b"x")
        code, _, err = run(["generate", "--engine", "power_bi", "--source", str(abf), "--kind", "abf",
                            "--output-dir", str(self.tmp / "out"), "--pbi-tools", "C:/tools/pbi-tools.exe"])
        self.assertEqual(code, 2)
        self.assertIn("cannot be combined with --pbi-tools", err)

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
