"""The catalogue link is UI-only render context: absent unless the writer of a catalogue passes it."""
import json
import tempfile
import unittest
from pathlib import Path

from pbidocgen.renderer import build_payload, render_html
from pbidocgen.model_parser import parse_model


def payload(root: Path) -> dict:
    bim = root / "m.bim"
    bim.write_text(json.dumps({"model": {"tables": [{"name": "T", "columns": [{"name": "A"}]}]}}))
    return build_payload(parse_model(bim), None, None, "M")


class RenderContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_document_has_no_catalogue_link(self):
        html = render_html(payload(self.root), self.root / "doc.html").read_text(encoding="utf-8")
        self.assertIn("const RENDER_CONTEXT = null || {};", html)
        self.assertNotIn("pbi-home.html", html)

    def test_catalogue_link_is_rendered_only_from_the_context(self):
        html = render_html(payload(self.root), self.root / "doc.html", catalog_href="pbi-home.html#reports").read_text(encoding="utf-8")
        self.assertIn('const RENDER_CONTEXT = {"catalogHref": "pbi-home.html#reports"} || {};', html)
        data = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
        self.assertNotIn("catalogHref", data)

    def test_catalogue_link_must_be_a_file_beside_the_document(self):
        for bad in ("https://example.com/home.html", "../home.html", "javascript:alert(1)", "home.html#a b", "/home.html", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                render_html(payload(self.root), self.root / "doc.html", catalog_href=bad)


if __name__ == "__main__":
    unittest.main()
