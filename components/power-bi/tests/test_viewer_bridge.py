"""The library viewer bridge (bi-doc-viewer protocol v1) ships in every page."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.renderer import build_payload, render_html  # noqa: E402


class ViewerBridge(unittest.TestCase):
    def test_registered_views_and_parent_only(self):
        with tempfile.TemporaryDirectory() as d:
            bim = Path(d) / "model.bim"
            bim.write_text(json.dumps({"model": {"name": "M", "tables": [{"name": "T", "columns": [{"name": "C"}]}]}}),
                           encoding="utf-8")
            out = Path(d) / "m.html"
            render_html(build_payload(parse_model(bim), None, None, "M"), out)
            text = out.read_text(encoding="utf-8")
        views = re.search(r"const VIEWER_VIEWS = \{(.*?)\n\};", text, re.S).group(1)
        self.assertEqual(sorted(re.findall(r'"(pbi\.[a-z]+)"', views)),
                         ["pbi.measure", "pbi.overview", "pbi.page", "pbi.source", "pbi.table"])
        self.assertIn("if(ev.source!==window.parent) return;", text)
        self.assertIn('m.protocol!=="bi-doc-viewer"', text)
        self.assertIn("if(window.parent===window", text)
        self.assertIn("if(DATA.published) return rDocumentationReadOnly();", text)
        self.assertIn("published, read-only copy", text)
        self.assertIn('if(window.location.hash && window.parent===window)window.history.pushState', text)


if __name__ == "__main__":
    unittest.main()
