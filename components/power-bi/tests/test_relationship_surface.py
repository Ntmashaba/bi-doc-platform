"""UI rework, Change 7: the Model view relationship surface, checked in the generated script.

One plain-SVG surface: tables as boxes, relationships as lines carrying cardinality and cross-filter direction;
selecting a table keeps its relationships, fades the rest and fills the panel beside the diagram. The
relationships list is a tab of its own. Real-browser checks are in browser_relationships.cjs.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.renderer import SCRIPTS, STYLES, build_payload, render_html  # noqa: E402


def table(name, columns, kind="m", **more):
    source = ({"type": "calculated", "expression": "CALENDAR(DATE(2020,1,1), DATE(2020,12,31))"} if kind == "calculated"
              else {"type": "m", "expression": f'let S = Sql.Database("s", "d"), T = S{{[Schema="dbo",Item="{name}"]}}[Data] in T'})
    return {"name": name, "columns": [c if isinstance(c, dict) else {"name": c, "sourceColumn": c} for c in columns],
            "partitions": [{"name": name, "source": source}], **more}


def rel(name, a, b, **more):
    (from_table, from_column), (to_table, to_column) = a, b
    return {"name": name, "fromTable": from_table, "fromColumn": from_column, "toTable": to_table, "toColumn": to_column, **more}


def doc():
    return {"model": {"name": "Surface", "tables": [
        table("Sales", ["SalesKey", "CustomerKey", "ProductKey", "OrderDate", "Amount",
                        {"name": "ShipDate", "type": "calculated", "expression": "[OrderDate] + 3"}],
              measures=[{"name": "Total", "expression": "SUM(Sales[Amount])"}]),
        table("Returns", ["SalesKey", "ProductKey", "Qty"], measures=[{"name": "Returned", "expression": "SUM(Returns[Qty])"}]),
        table("Customer", ["CustomerKey", "GeoKey", "Name"]),
        table("Product", ["ProductKey", "CategoryKey", "Name"]),
        table("Category", ["CategoryKey", "Name"]),
        table("Date", ["Date", "Year"], dataCategory="Time"),
        table("Geography", ["GeoKey", "Country"]),
        table("Notes & <more>", ["Text"]),
        table("LocalDateTable_1", [{"name": "Date", "type": "calculatedTableColumn", "sourceColumn": "[Date]"}], kind="calculated",
              isHidden=True, annotations=[{"name": "__PBI_LocalDateTable", "value": "true"}])],
        "relationships": [
            rel("r1", ("Sales", "CustomerKey"), ("Customer", "CustomerKey")),
            rel("r2", ("Sales", "ProductKey"), ("Product", "ProductKey")),
            rel("r3", ("Product", "CategoryKey"), ("Category", "CategoryKey")),
            rel("r4", ("Sales", "OrderDate"), ("Date", "Date")),
            rel("r5", ("Sales", "ShipDate"), ("Date", "Date"), isActive=False),
            rel("r6", ("Returns", "ProductKey"), ("Product", "ProductKey"), crossFilteringBehavior="bothDirections"),
            rel("r7", ("Customer", "GeoKey"), ("Geography", "GeoKey"), crossFilteringBehavior="automatic"),
            rel("auto", ("Sales", "OrderDate"), ("LocalDateTable_1", "Date")),
            rel("r8", ("Returns", "SalesKey"), ("Sales", "SalesKey"))]}}


class Surface(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        source = self.tmp / "model.bim"
        source.write_text(json.dumps(doc()), encoding="utf-8")
        self.html = render_html(build_payload(parse_model(source), None, None, "Surface"), self.tmp / "surface.html")

    @unittest.skipUnless(shutil.which("node"), "Node needed to run the generated script")
    def test_the_surface_the_panel_and_the_list(self):
        script = Path(__file__).with_name("check_relationships.cjs")
        result = subprocess.run(["node", str(script), str(self.html)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_plain_svg_with_no_external_library(self):
        """The document stays one standalone file: no script, style, font or image is fetched from anywhere."""
        html = self.html.read_text(encoding="utf-8")
        self.assertIn("relationships.js", SCRIPTS)
        self.assertIn("relationships.css", STYLES)
        self.assertEqual(re.findall(r"<script\b[^>]*\bsrc\s*=", html, re.I), [])
        self.assertEqual(re.findall(r"<link\b[^>]*\bhref\s*=\s*[\"']?(?:https?:)?//", html, re.I), [])
        self.assertEqual(re.findall(r"@import|url\(\s*[\"']?(?:https?:)?//", html, re.I), [])
        part = (Path(__file__).resolve().parents[1] / "pbidocgen" / "relationships.js").read_text(encoding="utf-8")
        self.assertNotRegex(part, r"\bimport\s|require\(|https?://|\bd3\b|cytoscape|mermaid")
        self.assertIn("<svg", part)


if __name__ == "__main__":
    unittest.main()
