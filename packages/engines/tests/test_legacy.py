"""Legacy engine HTML (no manifest) converts to a safe shared artifact, or is refused."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import ADF_MARKERS, PBI_MARKERS, adf_factory, pbi_model  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines import adf, legacy, power_bi  # noqa: E402
from bidoc_engines.convert import UnsupportedProjection  # noqa: E402


class Legacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.adf_html = adf.render(adf.load(adf_factory(cls.tmp / "f"), "adf_git")).encode()
        cls.pbi_html = power_bi.render(power_bi.load(pbi_model(cls.tmp / "m"), "bim")).encode()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_both_engines_convert_with_code_withheld(self):
        for html, markers, kind in ((self.adf_html, ADF_MARKERS, "adf"), (self.pbi_html, PBI_MARKERS, "power_bi")):
            self.assertIn(markers["sql_literal"].encode(), html)            # the legacy page carries the code
            artifact, manifest = legacy.convert(html, {"title": "Old doc", "environment": "Prod", "tags": ["x"]})
            validate_artifact(artifact)
            self.assertEqual((manifest["document_type"], manifest["source"]["kind"]), (kind, "legacy"))
            self.assertEqual(manifest["projection"]["profile"], "shared")
            self.assertEqual(manifest["publication"]["environment_key"], "production")
            self.assertEqual(manifest["title"], "Old doc")
            for value in markers.values():
                self.assertNotIn(value.encode(), artifact, value)

    def test_identity_is_new_unless_given(self):
        _, a = legacy.convert(self.adf_html, {})
        _, b = legacy.convert(self.adf_html, {})
        self.assertNotEqual(a["document_id"], b["document_id"])
        _, c = legacy.convert(self.adf_html, {}, document_id=a["document_id"], asset_id=a["publication"]["asset_id"])
        self.assertEqual((c["document_id"], c["publication"]["asset_id"]),
                         (a["document_id"], a["publication"]["asset_id"]))

    def test_unknown_or_ambiguous_html_is_refused(self):
        bad = [b"<html><body>hello</body></html>",
               self.adf_html.replace(b"const DATA = ", b"const DATA = 1; const DATA = ", 1),
               self.adf_html.replace(b'"schemaVersion": 2', b'"schemaVersion": 99', 1)
               .replace(b'"schemaVersion":2', b'"schemaVersion":99', 1),
               self.adf_html + self.pbi_html]
        for html in bad:
            with self.assertRaises(UnsupportedProjection):
                legacy.convert(html, {})

    def test_metadata_is_checked(self):
        self.assertEqual(legacy.parse_metadata('{"owner": "Ops"}'), {"owner": "Ops"})
        for raw in ('{"colour": "red"}', '{"tags": "x"}', "[1]"):
            with self.assertRaises(ValueError):
                legacy.parse_metadata(raw)


if __name__ == "__main__":
    unittest.main()
