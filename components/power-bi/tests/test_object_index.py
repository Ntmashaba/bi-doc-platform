"""Object identity and the search index embedded in a generated document (UI rework, Change 1)."""
import copy
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_column_usage import raw_model, report_fixture
from pbidocgen import object_index
from pbidocgen.linker import link
from pbidocgen.model_parser import parse_model
from pbidocgen.renderer import build_derived, build_payload, render_html

TAG = "1b2c3d4e-0000-4000-8000-00000000000"


def embedded(text, name):
    """The JSON literal assigned to `const <name>` in a rendered page."""
    start = re.search(r"\bconst " + name + r" = ", text).end()
    return json.JSONDecoder().raw_decode(text, start)[0]


class Identity(unittest.TestCase):
    def test_lineage_tags_first_then_names(self):
        self.assertEqual(object_index.table_id({"name": "Sales", "lineageTag": TAG + "1"}), "pbi:table:" + TAG + "1")
        self.assertEqual(object_index.table_id({"name": "Sales"}), "pbi:table:name:Sales")
        self.assertEqual(object_index.measure_id("Sales", {"name": "Total", "lineageTag": TAG + "2"}), "pbi:measure:" + TAG + "2")
        self.assertEqual(object_index.measure_id("Sales", {"name": "Total"}), "pbi:measure:name:Sales/Total")
        self.assertEqual(object_index.column_id("Sales", {"name": "Amount", "lineageTag": TAG + "3"}), "pbi:column:" + TAG + "3")
        self.assertEqual(object_index.column_id("Sales", {"name": "Amount"}), "pbi:column:name:Sales/Amount")
        self.assertEqual(object_index.query_id("Stage"), "pbi:query:name:Stage")
        self.assertEqual(object_index.query_id("Stage", TAG + "4"), "pbi:query:" + TAG + "4")
        self.assertEqual(object_index.page_id({"id": "ReportSection1", "name": "Overview"}), "pbi:page:ReportSection1")
        self.assertEqual(object_index.visual_id({"id": "p1"}, {"id": "v1"}), "pbi:visual:p1/v1")

    def test_a_source_id_is_stable_and_never_spells_its_location(self):
        fields = ["Excel workbook", r"C:\Users\alice\Budget.xlsx", "", "", "Budget.xlsx", r"C:\Users\alice\Budget.xlsx"]
        first = object_index.source_id(fields)
        self.assertEqual(first, object_index.source_id(list(fields)))
        self.assertNotEqual(first, object_index.source_id(fields[:5] + [r"C:\Users\bob\Budget.xlsx"]))
        self.assertRegex(first, r"^pbi:datasource:[0-9a-f]{16}$")


class SearchIndex(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def model(self, raw=None):
        path = self.root / "model.bim"
        path.write_text(json.dumps(raw or raw_model()), encoding="utf-8")
        return parse_model(path)

    def payload(self, raw=None, report=True):
        model = self.model(raw)
        rep = report_fixture() if report else None
        return build_payload(model, rep, link(model, rep) if rep else None, "Index")

    def entries(self, payload):
        index = object_index.build_search_index(payload)
        return [(index["kinds"][k], name, index["parents"][p] if p >= 0 else "", oid, *rest)
                for k, name, p, oid, *rest in index["items"]]

    def test_every_named_object_once_with_kind_and_parent(self):
        payload = self.payload()
        entries = self.entries(payload)
        by_kind = {}
        for kind, name, parent, *_ in entries:
            by_kind.setdefault(kind, []).append((name, parent))
        self.assertEqual(by_kind["table"], [("Dim", ""), ("Sales", "")])
        self.assertEqual(by_kind["calculated column"], [("Double", "Sales")])
        self.assertIn(("Amount", "Sales"), by_kind["column"])
        self.assertNotIn(("Double", "Sales"), by_kind["column"])
        self.assertEqual(len(by_kind["column"]), 9)                      # 8 loaded columns of Sales, 1 of Dim
        self.assertEqual(by_kind["measure"], [("Base", "Sales"), ("Formatted", "Sales"), ("Total", "Sales")])
        self.assertEqual(by_kind["query"], [("Sales", "")])
        self.assertEqual(by_kind["security role"], [("Regional", "")])
        self.assertEqual(by_kind["page"], [("Same / page", "Sales"), ("Same / page", "Sales")])
        self.assertEqual(len(by_kind["visual"]), 2)
        self.assertEqual(len(by_kind["data source"]), 1)
        ids = [e[3] for e in entries]
        self.assertEqual(len(ids), len(set(ids)))
        # Kinds appear in reading order and names are sorted within a kind.
        index = object_index.build_search_index(payload)
        self.assertEqual([k for k, *_ in index["items"]], sorted(k for k, *_ in index["items"]))
        self.assertEqual(index["v"], object_index.INDEX_VERSION)

    def test_kinds_come_from_metadata(self):
        raw = raw_model()
        raw["model"]["tables"] += [
            {"name": "Calendar", "columns": [{"name": "Date", "type": "calculatedTableColumn"}],
             "partitions": [{"name": "Calendar", "source": {"type": "calculated", "expression": "CALENDARAUTO()"}}]},
            {"name": "Time intelligence", "columns": [{"name": "Show as"}],
             "calculationGroup": {"calculationItems": [{"name": "YTD", "expression": "SELECTEDMEASURE()"}]},
             "partitions": [{"name": "p", "source": {"type": "calculationGroup"}}]}]
        kinds = {name: kind for kind, name, *_ in self.entries(self.payload(raw, report=False)) if "table" in kind or "group" in kind}
        self.assertEqual(kinds, {"Calendar": "calculated table", "Time intelligence": "calculation group",
                                 "Sales": "table", "Dim": "table"})

    def test_objects_sharing_a_tag_or_a_name_stay_distinct(self):
        raw = raw_model()
        raw["model"]["tables"][0]["lineageTag"] = raw["model"]["tables"][1]["lineageTag"] = TAG + "9"
        ids = [e[3] for e in self.entries(self.payload(raw, report=False)) if e[0] == "table"]
        self.assertEqual(ids, ["pbi:table:" + TAG + "9", "pbi:table:" + TAG + "9~2"])

    def test_report_only_and_model_only_documents(self):
        report = report_fixture()
        kinds = {e[0] for e in self.entries(build_payload(None, report, None, "Report only"))}
        self.assertEqual(kinds, {"page", "visual"})
        kinds = {e[0] for e in self.entries(self.payload(report=False))}
        self.assertNotIn("page", kinds)
        self.assertIn("column", kinds)

    def test_embedded_at_render_time_and_not_part_of_the_extract(self):
        payload = self.payload()
        self.assertNotIn("search", payload)
        text = render_html(payload, self.root / "doc.html").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"\bconst DATA = ", text)), 1)       # the legacy importer needs exactly one
        self.assertEqual(embedded(text, "DERIVED"), build_derived(dict(payload, documentationFilename="doc.html")))
        self.assertNotIn("search", embedded(text, "DATA"))

    def test_only_what_is_on_the_page_is_indexed(self):
        """A value removed from the payload before rendering (as a publication profile does) cannot be found."""
        payload = self.payload()
        shown = copy.deepcopy(payload)
        for row in shown["primarySources"]["rows"]:
            row["server"] = "server withheld"
        for row in shown["sourceQueries"]:
            row["mCode"] = "[query code withheld]"
        text = render_html(shown, self.root / "shared.html").read_text(encoding="utf-8")
        index = json.dumps(embedded(text, "DERIVED"))
        self.assertNotIn("Sql.Database", index)
        self.assertNotIn('"server"', index)
        self.assertIn("server withheld", index)

    def test_a_name_that_spells_a_template_slot_is_inert(self):
        raw = raw_model()
        hostile = ["/*__DATA__*/null", "/*__DERIVED__*/null", "/*__EXPLORER_JS__*/", "</script><script>window.pwned=1</script>"]
        for name in hostile:
            raw["model"]["tables"].append({"name": name, "columns": [{"name": name}]})
        payload = self.payload(raw, report=False)
        text = render_html(payload, self.root / "hostile.html").read_text(encoding="utf-8")
        names = [t["name"] for t in embedded(text, "DATA")["model"]["tables"]]
        self.assertEqual(names[-4:], hostile)
        indexed = [name for _, name, *_ in embedded(text, "DERIVED")["search"]["items"]]
        for name in hostile:
            self.assertIn(name, indexed)
        self.assertEqual(text.count("<script>"), 1)                         # the page's own script, nothing else
        self.assertNotIn("window.pwned=1</script>", text)

    @unittest.skipUnless(shutil.which("node"), "Node needed for generated JavaScript checks")
    def test_index_finder_and_routing_in_the_generated_script(self):
        model = self.model()
        for mode in ("combined", "semantic", "report"):
            with self.subTest(mode=mode):
                m = copy.deepcopy(model) if mode != "report" else None
                r = report_fixture() if mode != "semantic" else None
                html = render_html(build_payload(m, r, link(m, r) if m and r else None, mode), self.root / (mode + ".html"))
                result = subprocess.run(["node", str(Path(__file__).with_name("check_navigation.cjs")), str(html)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
