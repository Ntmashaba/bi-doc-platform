"""Durable object identity (lineageTag) survives parsing, and the embedded payload
cannot spell markup."""
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

TABLE_TAG = "1b2c3d4e-0000-4000-8000-000000000001"
MEASURE_TAG = "1b2c3d4e-0000-4000-8000-000000000002"
COLUMN_TAG = "1b2c3d4e-0000-4000-8000-000000000003"

TMDL = f"""table Sales
\tlineageTag: {TABLE_TAG}

\tmeasure Revenue = SUM(Sales[Amount])
\t\tlineageTag: {MEASURE_TAG}

\tcolumn Amount
\t\tdataType: decimal
\t\tlineageTag: {COLUMN_TAG}
\t\tsourceColumn: Amount

\tpartition Sales = m
\t\tmode: import
\t\tsource = let S = 1 in S
"""

BIM = {"model": {"name": "M", "tables": [{
    "name": "Sales", "lineageTag": TABLE_TAG,
    "columns": [{"name": "Amount", "lineageTag": COLUMN_TAG}],
    "measures": [{"name": "Revenue", "expression": "SUM(Sales[Amount])", "lineageTag": MEASURE_TAG}]}]}}


class LineageTags(unittest.TestCase):
    def check(self, model):
        table = model["tables"][0]
        self.assertEqual(table["lineageTag"], TABLE_TAG)
        self.assertEqual(table["columns"][0]["lineageTag"], COLUMN_TAG)
        self.assertEqual(table["measures"][0]["lineageTag"], MEASURE_TAG)

    def test_tmdl(self):
        with tempfile.TemporaryDirectory() as d:
            definition = Path(d) / "definition"
            (definition / "tables").mkdir(parents=True)
            (definition / "model.tmdl").write_text("model Model\n\tculture: en-US\n", encoding="utf-8")
            (definition / "tables" / "Sales.tmdl").write_text(TMDL, encoding="utf-8")
            self.check(parse_model(definition))

    def test_bim(self):
        with tempfile.TemporaryDirectory() as d:
            bim = Path(d) / "model.bim"
            bim.write_text(json.dumps(BIM), encoding="utf-8")
            self.check(parse_model(bim))

    def test_missing_tag_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            bim = Path(d) / "model.bim"
            bim.write_text(json.dumps({"model": {"name": "M", "tables": [{"name": "T", "columns": [{"name": "C"}]}]}}),
                           encoding="utf-8")
            table = parse_model(bim)["tables"][0]
            self.assertIsNone(table["lineageTag"])
            self.assertIsNone(table["columns"][0]["lineageTag"])


class EmbeddedPayload(unittest.TestCase):
    def test_payload_cannot_spell_markup(self):
        hostile = '</script><script id="pbidoc-manifest">x</script> & <!--'
        model = {"model": {"name": "M", "tables": [{"name": "T", "description": hostile, "columns": [{"name": "C"}]}]}}
        with tempfile.TemporaryDirectory() as d:
            bim = Path(d) / "model.bim"
            bim.write_text(json.dumps(model), encoding="utf-8")
            out = Path(d) / "m.html"
            render_html(build_payload(parse_model(bim), None, None, "M"), out)
            text = out.read_text(encoding="utf-8")
        blob = re.search(r"const DATA = (.*?);\n", text).group(1)
        self.assertNotIn("<", blob)
        self.assertNotIn(">", blob)
        self.assertEqual(json.loads(blob)["model"]["tables"][0]["description"], hostile)
        self.assertEqual(text.count('id="pbidoc-manifest"'), 0)


if __name__ == "__main__":
    unittest.main()
