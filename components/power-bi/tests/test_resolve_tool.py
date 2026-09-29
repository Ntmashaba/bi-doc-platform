"""Which PBIX extractor the engine's own command line uses when none is named."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pbidocgen import pbix_batch


class ResolveToolTests(unittest.TestCase):
    def test_nothing_named_and_no_pbi_tools_on_path_uses_the_portable_reader(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(pbix_batch.resolve_tool(None), "pbixray")

    def test_a_pbi_tools_on_path_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "pbi-tools.exe"
            exe.write_bytes(b"")
            with mock.patch("shutil.which", return_value=str(exe)):
                self.assertEqual(Path(pbix_batch.resolve_tool(None)).name, "pbi-tools.exe")

    def test_pbixray_can_be_asked_for_by_name(self):
        self.assertEqual(pbix_batch.resolve_tool("pbixray"), "pbixray")

    def test_a_named_pbi_tools_that_does_not_exist_is_an_error_not_a_fallback(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ValueError):
                pbix_batch.resolve_tool("C:/nowhere/pbi-tools.exe")

    def test_no_portable_reader_and_no_pbi_tools_is_the_original_error(self):
        with mock.patch("shutil.which", return_value=None), mock.patch("pbidocgen.portable.available", return_value=False):
            with self.assertRaisesRegex(ValueError, "pbi-tools Desktop was not found"):
                pbix_batch.resolve_tool(None)


if __name__ == "__main__":
    unittest.main()
