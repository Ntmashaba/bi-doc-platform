"""Which PBIX extractor the engine's own command line uses; it follows the same rules as the platform's policy."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pbidocgen import pbix_batch

RUNTIME = "pbidocgen.pbi_tools_runtime"
PORTABLE_OK = {"installed": "0.15.5", "supported": ">=0.15.0,<0.16", "ok": True, "reason": None}
PORTABLE_MISSING = {"installed": None, "supported": ">=0.15.0,<0.16", "ok": False,
                    "reason": "pbixray is not installed; portable extraction needs pbixray>=0.15.0,<0.16"}


class ResolveToolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.exe = Path(tmp.name) / "pbi-tools.exe"
        self.exe.write_bytes(b"MZ")

    def resolve(self, value=None, *, on_path=None, unusable=None, portable=PORTABLE_OK):
        """resolve_backend with the PATH lookup, the pbi-tools usability check and pbixray's status stood in."""
        with mock.patch("shutil.which", return_value=on_path), \
                mock.patch(f"{RUNTIME}.unusable_reason", return_value=unusable), \
                mock.patch("pbidocgen.portable.status", return_value=portable):
            return pbix_batch.resolve_backend(value)

    def test_nothing_named_and_no_pbi_tools_on_path_uses_the_portable_reader(self):
        self.assertEqual(self.resolve(), ("pbixray", None))

    def test_a_usable_pbi_tools_on_path_is_preferred(self):
        tool, note = self.resolve(on_path=str(self.exe))
        self.assertEqual((Path(tool).name, note), ("pbi-tools.exe", None))

    def test_an_implicit_pbi_tools_that_cannot_run_falls_back_and_says_why(self):
        tool, note = self.resolve(on_path=str(self.exe), unusable="PBIX extraction with pbi-tools runs on Windows only")
        self.assertEqual(tool, "pbixray")
        self.assertIn("Windows only", note)

    def test_pbixray_can_be_asked_for_by_name(self):
        self.assertEqual(self.resolve("pbixray", on_path=str(self.exe)), ("pbixray", None))

    def test_pbixray_asked_for_by_name_but_unavailable_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.resolve("pbixray", portable=PORTABLE_MISSING)

    def test_a_named_pbi_tools_that_does_not_exist_is_an_error_not_a_fallback(self):
        with self.assertRaisesRegex(ValueError, "pbi-tools Desktop was not found at"):
            self.resolve("C:/nowhere/pbi-tools.exe")

    def test_a_named_pbi_tools_that_exists_but_cannot_run_is_an_error_not_a_fallback(self):
        with self.assertRaisesRegex(ValueError, "Windows only"):
            self.resolve(str(self.exe), on_path=str(self.exe), unusable="PBIX extraction with pbi-tools runs on Windows only")

    def test_no_portable_reader_and_no_pbi_tools_is_the_original_error_plus_the_reason(self):
        with self.assertRaisesRegex(ValueError, "pbi-tools Desktop was not found") as caught:
            self.resolve(portable=PORTABLE_MISSING)
        self.assertIn("portable reader is not available either", str(caught.exception))

    def test_an_unusable_pbi_tools_and_no_portable_reader_reports_the_pbi_tools_problem(self):
        with self.assertRaisesRegex(ValueError, "Windows only"):
            self.resolve(on_path=str(self.exe), unusable="PBIX extraction with pbi-tools runs on Windows only",
                         portable=PORTABLE_MISSING)

    def test_resolve_tool_returns_only_the_tool(self):
        with mock.patch.object(pbix_batch, "resolve_backend", return_value=("pbixray", "a note")):
            self.assertEqual(pbix_batch.resolve_tool(None), "pbixray")


class RuntimeChecks(unittest.TestCase):
    """The shared checks in pbi_tools_runtime, on real files."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_file_checks(self):
        from pbidocgen import pbi_tools_runtime as runtime
        self.assertIn("was not found at", runtime.executable_problem(self.dir / "nope.exe"))
        wrapper = self.dir / "pbi-tools.cmd"
        wrapper.write_text("@echo off")
        self.assertIn("script wrapper", runtime.executable_problem(wrapper))
        core = self.dir / "pbi-tools.core.exe"
        core.write_bytes(b"MZ")
        self.assertIn("not pbi-tools.core", runtime.executable_problem(core))
        ok = self.dir / "pbi-tools.exe"
        ok.write_bytes(b"MZ")
        self.assertIsNone(runtime.executable_problem(ok))

    def test_prerequisites_follow_the_platform(self):
        from pbidocgen import pbi_tools_runtime as runtime
        with mock.patch("platform.system", return_value="Linux"):
            self.assertIn("Windows only", runtime.prerequisite_problem())
        with mock.patch("platform.system", return_value="Windows"), mock.patch.object(runtime, "power_bi_desktop", return_value=None):
            self.assertIn("Power BI Desktop", runtime.prerequisite_problem())
        with mock.patch("platform.system", return_value="Windows"), mock.patch.object(runtime, "power_bi_desktop", return_value="2.130"):
            self.assertIsNone(runtime.prerequisite_problem())

    def test_a_file_that_cannot_be_started_is_reported_and_cached(self):
        from pbidocgen import pbi_tools_runtime as runtime
        broken = self.dir / "broken.exe"
        broken.write_bytes(b"")
        runtime._launch_cache.clear()
        problem = runtime.launch_problem(broken)
        self.assertIn("could not be started", problem)
        with mock.patch.object(runtime.subprocess, "Popen", side_effect=AssertionError("must be cached")):
            self.assertEqual(runtime.launch_problem(broken), problem)


if __name__ == "__main__":
    unittest.main()
