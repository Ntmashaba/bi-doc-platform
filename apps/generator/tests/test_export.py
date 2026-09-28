"""Portable offline export (B12): contents, partial exports, refusals, rebuilds."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
sys.path.insert(0, str(ROOT / "apps" / "library" / "tests"))
from fixtures import adf_factory  # noqa: E402
from test_derived import pbi_model  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_generator.cli import main  # noqa: E402
from bidoc_generator.export import MARKER, ExportError, export_library  # noqa: E402


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.out = cls.tmp / "out"
        cls.ids = {}
        for engine, src, kind in (("adf", adf_factory(cls.tmp / "factory"), "adf_git"),
                                  ("power_bi", pbi_model(cls.tmp / "pbi"), "bim")):
            r = generate(GenerateRequest(engine=engine, source_path=str(src), source_kind=kind,
                                         output_dir=str(cls.out), environment="Production"))
            assert r.status == "completed", r.errors
            cls.ids[engine] = r.document_id

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def snapshot(self, folder):
        html = (folder / "index.html").read_text(encoding="utf-8")
        data = re.search(r'<script type="application/json" id="snapshot">(.*?)</script>', html, re.S).group(1)
        return html, json.loads(data)

    def test_complete_export(self):
        dest = self.tmp / "complete"
        meta = export_library([self.out], dest)
        self.assertEqual(meta["format"], "bidoc-portable/1")
        self.assertEqual({d["document_id"] for d in meta["documents"]}, set(self.ids.values()))
        self.assertGreater(meta["relationships"], 0)
        html, snap = self.snapshot(dest)
        self.assertIn("Content-Security-Policy", html)
        self.assertNotRegex(html, r"<script[^>]+src=")
        self.assertEqual(snap["not_included"], [])
        for d in meta["documents"]:
            validate_artifact((dest / d["file"]).read_bytes())

    def test_partial_export_marks_the_rest_not_included(self):
        dest = self.tmp / "partial"
        meta = export_library([self.out], dest, only=[self.ids["adf"]])
        self.assertEqual([d["document_id"] for d in meta["documents"]], [self.ids["adf"]])
        _, snap = self.snapshot(dest)
        self.assertEqual([d["document_id"] for d in snap["not_included"]], [self.ids["power_bi"]])
        self.assertFalse((dest / "documents" / f"{self.ids['power_bi']}.html").exists())
        self.assertTrue(snap["relationships"])

    def test_refusals(self):
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "keep.txt").write_text("mine")
        with self.assertRaisesRegex(ExportError, "not empty"):
            export_library([self.out], busy)
        with self.assertRaisesRegex(ExportError, "not in the inputs"):
            export_library([self.out], self.tmp / "x", only=["nope"])
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ExportError, "no documents"):
            export_library([empty], self.tmp / "y")

    def test_rebuild_replaces_previous_snapshot(self):
        dest = self.tmp / "rebuild"
        export_library([self.out], dest)
        export_library([self.out, dest], dest, only=[self.ids["power_bi"]])   # its own index.html is skipped
        self.assertEqual(sorted(p.name for p in (dest / "documents").iterdir()), [f"{self.ids['power_bi']}.html"])
        self.assertTrue((dest / MARKER).exists())

    def test_cli(self):
        dest = self.tmp / "cli"
        self.assertEqual(main(["export-library", str(dest), str(self.out), "--title", "Team <docs>"]), 0)
        html, snap = self.snapshot(dest)
        self.assertIn("<title>Team &lt;docs&gt;</title>", html)
        self.assertEqual(snap["title"], "Team <docs>")
        self.assertNotEqual(main(["export-library", str(self.tmp / "z"), str(self.out), "--only", "nope"]), 0)


if __name__ == "__main__":
    unittest.main()
