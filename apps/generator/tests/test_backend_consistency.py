"""The same machine and configuration must give the same PBIX backend at every entry point.

Each scenario fixes the machine's state (which pbi-tools is configured, whether it can run, which pbixray is installed) with
deterministic mocks of the platform prerequisite and launch checks, then asks every entry point what it would use:
`bidoc generate`, `bidoc batch` (and its siblings), the desktop app, the worker's advertised capabilities, and
`bidoc doctor`. The assertion is that they all agree, not that a helper returns a value.
"""
import contextlib
import importlib.metadata
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bidoc_generator import backend, cli  # noqa: E402
from bidoc_generator.batch import Options  # noqa: E402
from bidoc_generator.desktop.app import build_runner  # noqa: E402
from bidoc_generator.doctor import diagnose  # noqa: E402
from bidoc_generator.extract import ExtractionError, extract_pbix  # noqa: E402
from bidoc_generator.history import History  # noqa: E402
from bidoc_generator.worker import Worker  # noqa: E402

RUNTIME = "pbidocgen.pbi_tools_runtime"
PORTABLE, TOOL = "pbixray", "pbi-tools"


def label(tool):
    """Which backend a tool value stands for."""
    if not tool:
        return None
    return PORTABLE if tool == PORTABLE else TOOL


class Machine(unittest.TestCase):
    """A machine with a configured state; `observe()` reports what each entry point decides."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["BIDOC_HOME"] = str(self.tmp / "home")
        self.addCleanup(os.environ.pop, "BIDOC_HOME", None)
        os.environ.pop("BIDOC_PBI_TOOLS", None)
        self.pbix = self.tmp / "r.pbix"
        self.pbix.write_bytes(b"PK")
        self.exe = self.tmp / "pbi-tools.exe"
        self.exe.write_bytes(b"MZ")                      # a file: what "exists but may not run" means

    @contextlib.contextmanager
    def machine(self, *, implicit=None, prerequisite=None, launch=None, pbixray="ok"):
        """implicit: the configured/PATH pbi-tools (or None). prerequisite / launch: the reason each check fails, or
        None for success; launch="real" runs the real launch probe. pbixray: "ok", "unsupported" or "missing"."""
        version = {"ok": "0.15.5", "unsupported": "0.16.0"}.get(pbixray)
        if pbixray == "missing":
            fake = mock.Mock(side_effect=importlib.metadata.PackageNotFoundError("pbixray"))
        else:
            fake = mock.Mock(return_value=version)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(backend, "implicit_pbi_tools", return_value=implicit))
            stack.enter_context(mock.patch(f"{RUNTIME}.prerequisite_problem", return_value=prerequisite))
            if launch != "real":
                stack.enter_context(mock.patch(f"{RUNTIME}.launch_problem", return_value=launch))
            stack.enter_context(mock.patch("importlib.metadata.version", fake))
            yield

    def generate(self, *args, kind="pbix", source=None):
        """Run `bidoc generate` up to extraction. Returns (exit code, stderr, tool handed to extraction or None)."""
        captured = []

        def stop(source_path, workspace, tool, **kw):
            captured.append(tool)
            raise ExtractionError("EXTRACTION_FAILED", "stop here")
        argv = ["generate", "--engine", "power_bi", "--source", str(source or self.pbix), "--kind", kind,
                "--output-dir", str(self.tmp / "out"), *args]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("bidoc_generator.extract.extract_pbix", side_effect=stop), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, err.getvalue(), captured[0] if captured else None

    def observe(self, explicit=None):
        """What each entry point would use for a PBIX, as a dict of backend labels (None = PBIX unavailable)."""
        report = diagnose(explicit)
        runner = cli._runner(explicit)
        desktop = build_runner(explicit)
        for r in (runner, desktop):
            self.addCleanup(r.shutdown)
        worker = Worker(object(), pbi_tools=explicit).readiness()
        code, err, generated = self.generate(*(["--pbi-tools", explicit] if explicit else []))
        advertised = "pbix" in worker["input_types"]
        return {
            "doctor": label(report["pbi_tools"]),
            "batch": label(runner.pbi_tools) if runner.pbix_ready is None else None,
            "desktop": label(desktop.pbi_tools) if desktop.pbix_ready is None else None,
            "worker": (PORTABLE if str(worker["extractor_version"]).startswith(PORTABLE) else TOOL) if advertised else None,
            "generate": label(generated),
            "_report": report, "_runner": runner, "_worker": worker, "_generate": (code, err),
        }

    def assertAgree(self, seen, expected):
        decisions = {k: v for k, v in seen.items() if not k.startswith("_")}
        self.assertEqual(decisions, dict.fromkeys(decisions, expected), decisions)

    # ---- the scenarios ---------------------------------------------------------------------------------------

    def test_no_pbi_tools_configured_and_pbixray_supported_uses_pbixray_everywhere(self):
        with self.machine(implicit=None):
            seen = self.observe()
        self.assertAgree(seen, PORTABLE)
        self.assertTrue(seen["_report"]["inputs"]["pbix"]["available"])

    def test_a_usable_configured_pbi_tools_is_used_everywhere(self):
        with self.machine(implicit=str(self.exe)):
            seen = self.observe()
        self.assertAgree(seen, TOOL)
        self.assertIsNone(seen["_report"]["pbix_backend"]["fallback"])

    def test_an_implicit_pbi_tools_that_cannot_run_here_falls_back_everywhere_and_says_so(self):
        with self.machine(implicit=str(self.exe), prerequisite="PBIX extraction with pbi-tools runs on Windows only"):
            seen = self.observe()
        self.assertAgree(seen, PORTABLE)
        code, err = seen["_generate"]
        self.assertIn("cannot be used here", err)
        self.assertIn("Windows only", err)
        self.assertIn("cannot be used here", seen["_report"]["pbix_backend"]["fallback"])
        self.assertIn("cannot be used here", seen["_worker"]["readiness_detail"])       # the worker says so as well

    def test_an_existing_but_non_launchable_executable_is_not_advertised_or_selected(self):
        broken = self.tmp / "broken-pbi-tools.exe"
        broken.write_bytes(b"")                          # exists, cannot be started on any platform
        with self.machine(implicit=str(broken), launch="real"):
            seen = self.observe()
        self.assertAgree(seen, PORTABLE)                 # the real launch probe rejected it: portable everywhere
        self.assertIn("could not be started", seen["_report"]["pbix_backend"]["pbi_tools_problem"])

    def test_the_launch_probe_is_bounded(self):
        slow = self.tmp / "hangs.exe"
        slow.write_bytes(b"")
        class Hangs:
            killed = False

            def wait(self, timeout=None):
                import subprocess
                if not self.killed:
                    raise subprocess.TimeoutExpired("hangs", timeout)

            def kill(self):
                self.killed = True
        proc = Hangs()
        from pbidocgen import pbi_tools_runtime as runtime
        runtime._launch_cache.clear()
        with mock.patch.object(runtime.subprocess, "Popen", return_value=proc):
            problem = runtime.launch_problem(slow, timeout=0.01)
        runtime._launch_cache.clear()
        self.assertIn("did not respond within", problem)
        self.assertTrue(proc.killed)

    def test_an_explicit_invalid_pbi_tools_is_an_error_and_never_replaced_by_pbixray(self):
        missing = str(self.tmp / "nowhere" / "pbi-tools.exe")
        with self.machine(implicit=None):                 # pbixray is fine; the explicit request must still fail
            seen = self.observe(missing)
        self.assertAgree(seen, None)
        code, err = seen["_generate"]
        self.assertEqual(code, 3)
        self.assertIn("pbi-tools cannot be used", err)
        self.assertIn("was not found at", err)
        self.assertNotIn("portable", err.lower().replace("the portable reader", ""))   # no silent switch
        self.assertFalse(seen["_report"]["inputs"]["pbix"]["available"])
        self.assertIn("PBIX not available", seen["_worker"]["readiness_detail"])

    def test_an_explicit_pbi_tools_that_exists_but_cannot_run_is_also_an_error(self):
        with self.machine(implicit=None, prerequisite="PBIX extraction with pbi-tools runs on Windows only"):
            seen = self.observe(str(self.exe))
        self.assertAgree(seen, None)
        self.assertIn("Windows only", seen["_generate"][1])

    def test_explicit_backend_pbi_tools_without_any_pbi_tools_is_actionable(self):
        with self.machine(implicit=None):
            code, err, tool = self.generate("--backend", "pbi-tools")
        self.assertEqual((code, tool), (3, None))
        self.assertIn("--pbi-tools EXE", err)
        self.assertIn("BIDOC_PBI_TOOLS", err)

    def test_explicit_pbixray_is_honoured_even_when_a_usable_pbi_tools_is_configured(self):
        with self.machine(implicit=str(self.exe)):
            self.assertEqual(self.generate("--backend", "pbixray")[2], PORTABLE)
            self.assertEqual(self.generate("--pbixray")[2], PORTABLE)

    def test_explicit_pbixray_that_is_unavailable_says_why(self):
        with self.machine(implicit=str(self.exe), pbixray="unsupported"):
            code, err, tool = self.generate("--backend", "pbixray")
        self.assertEqual((code, tool), (3, None))
        self.assertIn("validated only for", err)

    def test_pbixray_missing_and_no_pbi_tools_reports_pbix_unavailable_everywhere(self):
        with self.machine(implicit=None, pbixray="missing"):
            seen = self.observe()
        self.assertAgree(seen, None)
        self.assertIn("not installed", seen["_report"]["inputs"]["pbix"]["reason"])
        self.assertIn("not installed", seen["_generate"][1])
        self.assertIn("not installed", seen["_worker"]["readiness_detail"])
        self.assertIn("PBIP, model and ADF inputs still work", seen["_generate"][1])

    def test_unsupported_pbixray_and_an_unusable_pbi_tools_reports_both_reasons(self):
        with self.machine(implicit=str(self.exe), prerequisite="PBIX extraction with pbi-tools runs on Windows only",
                          pbixray="unsupported"):
            seen = self.observe()
        self.assertAgree(seen, None)
        reason = seen["_report"]["inputs"]["pbix"]["reason"]
        self.assertIn("Windows only", reason)
        self.assertIn("validated only for", reason)

    def test_the_worker_advertises_pbix_only_for_the_backend_it_selected(self):
        with self.machine(implicit=str(self.exe)):
            ready = Worker(object()).readiness()
        self.assertIn("pbix", ready["input_types"])
        self.assertEqual(ready["extractor_version"], str(self.exe))
        with self.machine(implicit=str(self.exe), prerequisite="PBIX extraction with pbi-tools runs on Windows only"):
            ready = Worker(object()).readiness()
        self.assertIn("pbix", ready["input_types"])
        self.assertTrue(ready["extractor_version"].startswith(PORTABLE))
        with self.machine(implicit=str(self.exe), prerequisite="PBIX extraction with pbi-tools runs on Windows only",
                          pbixray="missing"):
            ready = Worker(object()).readiness()
        self.assertEqual(ready["input_types"], ["pbip_zip"])              # the file exists; that is not enough

    def test_a_worker_job_fails_with_a_structured_error_when_the_backend_is_gone(self):
        from bidoc_generator.worker import _JobError
        with self.machine(implicit=None, pbixray="missing"):
            worker = Worker(object())
            with self.assertRaises(_JobError) as caught:
                worker._generate({"input_type": "pbix"}, self.pbix, self.tmp / "ws", lambda *_: None, mock.Mock())
        self.assertEqual(caught.exception.code, "PREREQUISITE_MISSING")

    # ---- ABF ----------------------------------------------------------------------------------------------------

    def test_abf_stays_portable_whatever_pbi_tools_is_configured(self):
        abf = self.tmp / "m.abf"
        abf.write_bytes(b"x")
        for implicit, extra in ((None, {}), (str(self.exe), {}),
                                (str(self.exe), {"prerequisite": "PBIX extraction with pbi-tools runs on Windows only"})):
            with self.subTest(implicit=implicit, extra=extra), self.machine(implicit=implicit, **extra):
                self.assertEqual(self.generate(kind="abf", source=abf)[2], PORTABLE)
                captured = []
                runner = build_runner(None)
                self.addCleanup(runner.shutdown)
                runner.pbi_tools = implicit               # a pbi-tools configured for the desktop/batch runner
                with mock.patch("bidoc_generator.batch.extract_pbix",
                                side_effect=lambda *a, **k: captured.append(a[2]) or (_ for _ in ()).throw(
                                    ExtractionError("EXTRACTION_FAILED", "stop"))):
                    runner.submit([str(abf)], Options(output_dir=str(self.tmp / "o")))
                    self.assertTrue(runner.wait(60))
                self.assertEqual(captured, [PORTABLE])

    def test_abf_with_an_incompatible_backend_option_is_rejected(self):
        abf = self.tmp / "m.abf"
        abf.write_bytes(b"x")
        with self.machine(implicit=None):
            for args in (["--pbi-tools", str(self.exe)], ["--backend", "pbi-tools"]):
                code, err, tool = self.generate(*args, kind="abf", source=abf)
                self.assertEqual((code, tool), (2, None), args)
                self.assertIn("ABF file cannot be combined with --pbi-tools", err)

    def test_abf_readiness_needs_only_the_portable_reader(self):
        with self.machine(implicit=None, pbixray="unsupported"):
            state = diagnose()["inputs"]["abf"]
        self.assertFalse(state["available"])
        self.assertIn("validated only for", state["reason"])

    # ---- extraction-time failures are structured ----------------------------------------------------------

    def test_a_launch_error_at_extraction_is_a_structured_error_not_a_raw_exception(self):
        broken = self.tmp / "broken.exe"
        broken.write_bytes(b"")
        with self.assertRaises(ExtractionError) as caught:
            extract_pbix(self.pbix, self.tmp / "ws", str(broken), timeout=5)
        self.assertEqual(caught.exception.code, "PREREQUISITE_MISSING")
        self.assertIn("could not be started", str(caught.exception))

    def test_readiness_is_not_a_promise_that_every_file_extracts(self):
        # The backend is ready; one file still fails to extract, and that failure is reported, not retried elsewhere.
        seen_tools = []

        def fail(source, workspace, tool, **kw):
            seen_tools.append(tool)
            raise ExtractionError("EXTRACTION_FAILED", "this file cannot be read")
        with self.machine(implicit=str(self.exe)):
            self.assertTrue(diagnose()["inputs"]["pbix"]["available"])
            with mock.patch("bidoc_generator.extract.extract_pbix", side_effect=fail):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(["generate", "--engine", "power_bi", "--source", str(self.pbix), "--kind", "pbix",
                                     "--output-dir", str(self.tmp / "out")])
        self.assertEqual(code, 4)
        self.assertEqual([label(t) for t in seen_tools], [TOOL])           # one attempt, no second backend

    # ---- the engine's own command line agrees --------------------------------------------------------------------

    def test_the_standalone_engine_command_line_agrees_with_the_platform_policy(self):
        from pbidocgen.pbix_batch import resolve_backend
        cases = [
            ("no tool", dict(implicit=None), None, PORTABLE),
            ("usable tool", dict(implicit=str(self.exe)), None, TOOL),
            ("implicit tool cannot run", dict(implicit=str(self.exe), prerequisite="Windows only"), None, PORTABLE),
        ]
        for name, kwargs, explicit, expected in cases:
            with self.subTest(name), self.machine(**kwargs), mock.patch("shutil.which", return_value=kwargs["implicit"]):
                platform_choice = label(backend.select_backend("auto", explicit).tool)
                engine_choice = label(resolve_backend(explicit)[0])
                self.assertEqual((platform_choice, engine_choice), (expected, expected))
        with self.machine(implicit=None, prerequisite="Windows only"), mock.patch("shutil.which", return_value=None):
            self.assertIsNone(backend.select_backend("auto", str(self.exe)).tool)   # explicit + cannot run: error
            with self.assertRaises(ValueError):
                resolve_backend(str(self.exe))


if __name__ == "__main__":
    unittest.main()
