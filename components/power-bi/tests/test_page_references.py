import copy
import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from test_column_usage import raw_model, field
from pbidocgen.model_parser import parse_model
from pbidocgen.report_parser import parse_report
from pbidocgen.linker import link
from pbidocgen.renderer import build_payload
from pbidocgen.column_usage import write_column_csv
from pbidocgen.agent_writer import build_agent_md
from pbidocgen.word_writer import render_docx


class PageReferenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        raw = raw_model()
        measures = raw["model"]["tables"][0].pop("measures")
        raw["model"]["tables"].append({"name": "Measures", "columns": [], "measures": measures})
        path = self.root / "model.bim"
        path.write_text(json.dumps(raw))
        self.model = parse_model(path)
        def page(pid, name, bindings):
            return {"id": pid, "name": name, "hidden": pid == "p_b", "isActive": pid == "p-b", "filters": [],
                    "visuals": [{"id": "visual", "type": "table", "title": None, "fields": bindings, "filters": []}]}
        self.report = {"name": "Sales report", "pages": [
            page("p-b", "Overview / detail", [field("Amount")]),
            page("p_b", "Overview / detail", [field("Total", "measure", "Measures")]),
            page("other", "Other", [field("ID", table="Dim")])],
            "reportFilters": [], "bookmarks": [{"name": "Saved", "fields": [field("BookmarkOnly")]}],
            "manifest": [], "warnings": []}

    def payload(self):
        m, r = copy.deepcopy(self.model), copy.deepcopy(self.report)
        return build_payload(m, r, link(m, r), "Sales")

    def test_table_and_column_rows_have_individual_page_usage(self):
        p = self.payload()
        rows = [r for r in p["tableSources"] if r["table"] == "Sales"]
        self.assertEqual({r["pageId"] for r in rows}, {"p-b", "p_b", ""})
        self.assertEqual(next(r for r in rows if r["pageId"] == "p-b")["usage"], "Direct")
        self.assertEqual(next(r for r in rows if r["pageId"] == "p_b")["usage"], "Via measures")
        self.assertTrue(all(r["report"] == "Sales report" for r in rows))
        amount = [r for r in p["columns"]["rows"] if r["column"] == "Amount"]
        self.assertEqual({(r["pageId"], r["pageUsage"]) for r in amount}, {("p-b", "Direct"), ("p_b", "Via measures")})
        self.assertTrue(all(r["report"] == "Sales report" for r in amount))
        self.assertTrue(all(r["pageId"] != "other" for r in amount))

    def test_measures_lineage_page_feeds_and_manifest_share_ids(self):
        p = self.payload()
        base = next(m for m in p["model"]["measures"] if m["name"] == "Base")
        self.assertEqual([r["pageId"] for r in base["pageUsage"]], ["p_b"])
        self.assertIn("[p_b]", base["usedBy"][0])
        lineage = next(r for r in p["linked"]["lineage"] if r["table"] == "Sales")
        self.assertEqual({r["pageId"] for r in lineage["pageUsage"]}, {"p-b", "p_b"})
        self.assertEqual(len(lineage["consumers"]), 2)
        self.assertIn("[p-b]", lineage["consumers"][0])
        feed = p["report"]["pages"][1]["feeds"]
        self.assertEqual(next(r for r in feed if r["table"] == "Sales")["usage"], "Via measures")
        manifest = next(r for r in p["linked"]["manifest"] if r["field"] == "Total")
        self.assertEqual(manifest["locations"][0]["pageId"], "p_b")
        self.assertEqual(manifest["locations"][0]["report"], "Sales report")

    def test_report_filters_expand_to_all_pages_and_unknown_bookmarks_do_not(self):
        self.report["reportFilters"] = [dict(field("Label"), level="report", target="Entire report", filterType="basic", raw=None)]
        p = self.payload()
        labels = [r for r in p["columns"]["rows"] if r["column"] == "Label"]
        self.assertEqual({r["pageId"] for r in labels}, {"p-b", "p_b", "other"})
        self.assertEqual({r["pageId"] for r in p["report"]["filterRows"]}, {"p-b", "p_b", "other"})
        bm = next(r for r in p["columns"]["rows"] if r["column"] == "BookmarkOnly")
        self.assertEqual(bm["pageId"], "")
        self.assertEqual(bm["pageScope"], "Bookmark/report scope only")

    def test_csv_word_agent_and_json_preserve_same_page_grain(self):
        p = self.payload()
        csv_path = write_column_csv(p["columns"], self.root / "cols.csv")
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        rows = [r for r in rows if r["Column"] == "Amount"]
        self.assertEqual({(r["Report"], r["Page ID"]) for r in rows}, {("Sales report", "p-b"), ("Sales report", "p_b")})
        parsed = json.loads(json.dumps(p))
        self.assertEqual(parsed["columns"]["rows"], p["columns"]["rows"])
        agent = build_agent_md(p)
        self.assertIn("| Sales report | Overview / detail | p_b | Sales[Amount] | Via measures |", agent)
        self.assertIn("Sales report / Overview / detail [p-b]", agent)
        docx = render_docx(p, self.root / "report.docx")
        with zipfile.ZipFile(docx) as z:
            root = ET.fromstring(z.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        def text(el):
            return "".join(el.itertext())
        # Find the column inventory and compare its exact grain with CSV.
        table = next(t for t in root.findall(".//w:tbl", ns)
                     if "Deletion assessment / measures" in text(t.find("w:tr", ns)))
        table_rows = [[text(c) for c in row.findall("w:tc", ns)] for row in table.findall("w:tr", ns)[1:]]
        amount = [r for r in table_rows if r[1] == "Sales[Amount]"]
        self.assertEqual({r[0] for r in amount}, {"Sales report / Overview / detail [p-b]", "Sales report / Overview / detail [p_b]"})
        self.assertEqual({r[2] for r in amount}, {"Direct", "Via measures"})

    def test_report_only_and_inner_definition_path_keep_page_identity(self):
        definition = self.root / "Sales.Report" / "definition"
        for pid in ("p-b", "p_b"):
            visual = definition / "pages" / pid / "visuals" / "v"
            visual.mkdir(parents=True)
            (visual.parent.parent / "page.json").write_text('{"displayName":"Overview"}')
            binding = {"Column": {"Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": "Amount"}}
            (visual / "visual.json").write_text(json.dumps({"visual": {"visualType": "card", "query": binding}}))
        (definition / "report.json").write_text('{"name":"Report"}')
        report = parse_report(definition)
        p = build_payload(None, report, None, "Report only")
        self.assertEqual(report["name"], "Sales")
        self.assertEqual({r["pageId"] for r in report["manifest"][0]["locations"]}, {"p-b", "p_b"})
        self.assertEqual(len(report["manifest"][0]["usedIn"]), 2)
        self.assertTrue(all(page["feeds"] for page in report["pages"]))
        self.assertIsNone(p["columns"])


if __name__ == "__main__":
    unittest.main()
