"""UI rework, Report view: page types, drillthrough fields, filter flags and bookmarks, on every report format.

Hidden, tooltip and drillthrough pages are told apart from the page settings each format records (page_types.py):
PBIR page.json (also inside a PBIX), the legacy layout (a PBIP report.json and a PBIX Layout) and pbi-tools'
Report/sections extract. A page whose settings the file does not hold is "page type not recorded", never an
ordinary page. The rendered Report view is checked in check_report_view.cjs and, in Chromium, browser_report_view.cjs.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pbidocgen import page_types, portable  # noqa: E402
from pbidocgen.agent_writer import build_agent_md  # noqa: E402
from pbidocgen.extracted_report import parse_extracted_report, parse_legacy_layout  # noqa: E402
from pbidocgen.pbix_batch import load_extracted  # noqa: E402
from pbidocgen.renderer import build_payload, render_html  # noqa: E402
from pbidocgen.report_parser import parse_report  # noqa: E402


def column(table, name):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


# ---------------------------------------------------------------- PBIR
PBIR_PAGES = {
    "home": {"name": "home", "displayName": "Home", "width": 1280, "height": 720},
    "tip": {"name": "tip", "displayName": "Sales tooltip", "type": "Tooltip", "visibility": "HiddenInViewMode",
            "pageBinding": {"name": "b-tip", "type": "Tooltip"}, "width": 320, "height": 240,
            "filterConfig": {"filters": [{"name": "f-tip", "field": column("Sales", "Amount"), "howCreated": "Drillthrough"}]}},
    "detail": {"name": "detail", "displayName": "Order details", "type": "Drillthrough",
               "pageBinding": {"name": "b-detail", "type": "Drillthrough", "parameters": []},
               "filterConfig": {"filters": [
                   {"name": "f-drill", "field": column("Dim", "ID"), "howCreated": "Drillthrough"},
                   {"name": "f-region", "field": column("Dim", "Region"), "howCreated": "User", "displayName": "Region filter",
                    "isHiddenInViewMode": True, "isLockedInViewMode": True},
                   {"name": "f-user", "field": column("Dim", "Name"), "howCreated": "User"}]}},
    # Written before page.json had a `type`: the binding alone says how the page is used.
    "bound": {"name": "bound", "displayName": "Bound only", "pageBinding": {"name": "b-bound", "type": "Drillthrough"}},
    "default": {"name": "default", "displayName": "Default binding", "pageBinding": {"name": "b-default", "type": "Default"}},
    "odd": {"name": "odd", "displayName": "Odd", "type": "Wallpaper"},
}


def pbir_report(root: Path) -> Path:
    definition = root / "Sales.Report" / "definition"
    for pid, page in PBIR_PAGES.items():
        (definition / "pages" / pid).mkdir(parents=True)
        (definition / "pages" / pid / "page.json").write_text(json.dumps(page), encoding="utf-8")
    (definition / "pages" / "pages.json").write_text(json.dumps({"pageOrder": list(PBIR_PAGES)}), encoding="utf-8")
    (definition / "report.json").write_text(json.dumps({"filterConfig": {"filters": [
        {"name": "f-all", "field": column("Sales", "Year"), "howCreated": "User"}]}}), encoding="utf-8")
    bookmarks = definition / "bookmarks"
    bookmarks.mkdir()
    # bookmarks.json orders the bookmarks and groups them; it is not a bookmark itself.
    (bookmarks / "bookmarks.json").write_text(json.dumps({"items": [
        {"name": "b2"}, {"name": "g1", "displayName": "Saved views", "children": ["b1"]}]}), encoding="utf-8")
    (bookmarks / "b1.bookmark.json").write_text(json.dumps({"name": "b1", "displayName": "First",
        "explorationState": {"version": "1.3", "activeSection": "detail", "sections": {}}}), encoding="utf-8")
    (bookmarks / "b2.bookmark.json").write_text(json.dumps({"name": "b2", "displayName": "Second",
        "explorationState": {"version": "1.3", "activeSection": "home", "sections": {}}}), encoding="utf-8")
    (bookmarks / "b3.bookmark.json").write_text(json.dumps({"name": "b3", "displayName": "Not in the metadata",
        "explorationState": {"version": "1.3", "sections": {}}}), encoding="utf-8")
    return definition.parent


# ---------------------------------------------------------------- legacy layout
def legacy_sections():
    """The same pages in the legacy layout: settings in each section's config, drillthrough fields as filters."""
    return [
        {"name": "ReportSection1", "displayName": "Home", "ordinal": 0, "config": "{}", "filters": "[]"},
        {"name": "ReportSection2", "displayName": "Sales tooltip", "ordinal": 1, "width": 320, "height": 240,
         "config": json.dumps({"type": 1, "visibility": 1}),
         "filters": json.dumps([{"name": "F1", "expression": column("Sales", "Amount"), "howCreated": 5}])},
        {"name": "ReportSection3", "displayName": "Order details", "ordinal": 2, "config": json.dumps({"visibility": 0}),
         "filters": json.dumps([{"name": "F2", "expression": column("Dim", "ID"), "howCreated": 5},
                                {"name": "F3", "expression": column("Dim", "Region"), "howCreated": 1}])},
        {"name": "ReportSection4", "displayName": "No settings", "ordinal": 3},
        {"name": "ReportSection5", "displayName": "Odd", "ordinal": 4, "config": json.dumps({"type": 2})},
        {"name": "ReportSection6", "displayName": "Hidden page", "ordinal": 5, "config": json.dumps({"visibility": 1})},
    ]


LEGACY_BOOKMARKS = [
    {"name": "Bm1", "displayName": "One", "explorationState": {"activeSection": "ReportSection3"}},
    {"name": "G", "displayName": "Saved views", "children": [
        {"name": "Bm2", "displayName": "Two", "explorationState": {"activeSection": "ReportSection1"}}]}]


def legacy_layout():
    return {"sections": legacy_sections(), "config": json.dumps({"bookmarks": LEGACY_BOOKMARKS}), "filters": "[]"}


EXPECTED = {   # display name: (pageType, hidden)
    "Home": ("page", False), "Sales tooltip": ("tooltip", True), "Order details": ("drillthrough", False),
    "No settings": (None, False), "Odd": ("other", False), "Hidden page": ("page", True)}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def types(self, report):
        return {p["name"]: (p.get("pageType"), p["hidden"]) for p in report["pages"]}

    def check_legacy(self, report):
        self.assertEqual(self.types(report), EXPECTED)
        pages = {p["name"]: p for p in report["pages"]}
        self.assertEqual(pages["Odd"]["pageTypeRaw"], "type 2")
        self.assertNotIn("pageTypeRaw", pages["Home"])
        # drillthrough fields: on the drillthrough page, and the tooltip fields of the tooltip page
        drill = {name: [f["field"] for f in p["filters"] if f.get("drillthrough")] for name, p in pages.items()}
        self.assertEqual(drill["Order details"], ["ID"])
        self.assertEqual(drill["Sales tooltip"], ["Amount"])
        # a filter the author added (howCreated 1, "User") is shown to readers: it is not hidden
        region = next(f for f in pages["Order details"]["filters"] if f["field"] == "Region")
        self.assertFalse(region["isHidden"])
        self.assertNotIn("drillthrough", region)


class Rules(unittest.TestCase):
    def test_pbir(self):
        pbir = page_types.pbir
        self.assertEqual(pbir({}), {"pageType": "page"})
        self.assertEqual(pbir({"type": "Tooltip"}), {"pageType": "tooltip"})
        self.assertEqual(pbir({"type": "Drillthrough"}), {"pageType": "drillthrough"})
        self.assertEqual(pbir({"pageBinding": {"name": "b", "type": "Tooltip"}}), {"pageType": "tooltip"})
        self.assertEqual(pbir({"pageBinding": {"name": "b", "type": "Drillthrough"}}), {"pageType": "drillthrough"})
        self.assertEqual(pbir({"pageBinding": {"name": "b", "type": "Default"}}), {"pageType": "page"})
        self.assertEqual(pbir({"type": "Wallpaper"}), {"pageType": "other", "pageTypeRaw": "type Wallpaper"})
        self.assertEqual(pbir({"pageBinding": {"type": "Popup"}}), {"pageType": "other", "pageTypeRaw": "pageBinding type Popup"})
        # drillthrough fields make a drillthrough page even where the page settings do not say so
        self.assertEqual(pbir({}, [{"drillthrough": True}]), {"pageType": "drillthrough"})

    def test_legacy(self):
        legacy, drill = page_types.legacy, [{"drillthrough": True}]
        self.assertEqual(legacy({}), {"pageType": "page"})
        self.assertEqual(legacy({"type": 1}), {"pageType": "tooltip"})
        self.assertEqual(legacy({"type": 1}, drill), {"pageType": "tooltip"}, "a tooltip page's fields are its tooltip fields")
        self.assertEqual(legacy({}, drill), {"pageType": "drillthrough"})
        self.assertEqual(legacy({"type": 2}), {"pageType": "other", "pageTypeRaw": "type 2"})
        self.assertEqual(legacy({"type": True}), {"pageType": "other", "pageTypeRaw": "type True"})
        # no page settings in the file: nothing is assumed
        self.assertEqual(legacy(None), {})
        self.assertEqual(legacy(None, drill), {"pageType": "drillthrough"})

    def test_how_a_filter_was_created(self):
        self.assertTrue(page_types.is_drillthrough_filter({"howCreated": 5}))
        self.assertTrue(page_types.is_drillthrough_filter({"howCreated": "Drillthrough"}))
        for other in ({"howCreated": 1}, {"howCreated": "User"}, {"howCreated": True}, {}):
            self.assertFalse(page_types.is_drillthrough_filter(other), other)

    def test_words(self):
        self.assertEqual([page_types.label(p) for p in ({"pageType": "page"}, {"pageType": "tooltip"},
                          {"pageType": "drillthrough"}, {"pageType": "other", "pageTypeRaw": "type 2"}, {})],
                         ["report page", "tooltip page", "drillthrough page", "page type not recognised (type 2)",
                          "page type not recorded"])
        self.assertEqual(page_types.flags({"pageType": "page", "hidden": False}), [])
        self.assertEqual(page_types.flags({"pageType": "tooltip", "hidden": True}), ["tooltip page", "hidden"])
        self.assertEqual(page_types.flags({"hidden": False}), ["page type not recorded"])


class Formats(Base):
    def test_pbir(self):
        report = parse_report(pbir_report(self.tmp))
        self.assertEqual([p["id"] for p in report["pages"]], list(PBIR_PAGES), "pages in report order")
        self.assertEqual(self.types(report), {"Home": ("page", False), "Sales tooltip": ("tooltip", True),
                                               "Order details": ("drillthrough", False), "Bound only": ("drillthrough", False),
                                               "Default binding": ("page", False), "Odd": ("other", False)})
        pages = {p["id"]: p for p in report["pages"]}
        filters = {f["name"]: f for f in pages["detail"]["filters"]}
        self.assertTrue(filters["f-drill"]["drillthrough"])
        self.assertEqual((filters["f-region"]["isHidden"], filters["f-region"].get("isLocked"), filters["f-region"]["displayName"]),
                         (True, True, "Region filter"))
        self.assertEqual((filters["f-user"]["isHidden"], "isLocked" in filters["f-user"], "drillthrough" in filters["f-user"]),
                         (False, False, False))
        self.assertTrue(pages["tip"]["filters"][0]["drillthrough"])
        # bookmarks: every bookmark file, none from bookmarks.json; in the file's order, with group and page
        self.assertEqual([(b["name"], b.get("group"), b.get("page")) for b in report["bookmarks"]],
                         [("Second", None, "home"), ("First", "Saved views", "detail"), ("Not in the metadata", None, None)])

    def test_pbir_inside_a_pbix(self):
        root = pbir_report(self.tmp / "src")
        pbix = self.tmp / "Sales.pbix"
        with zipfile.ZipFile(pbix, "w") as archive:
            for path in (root / "definition").rglob("*.json"):
                archive.write(path, "Report/definition/" + path.relative_to(root / "definition").as_posix())
        out = self.tmp / "extract"
        portable.extract(pbix, out)
        _, report = load_extracted(out, pbix, False)
        self.assertEqual(self.types(report)["Sales tooltip"], ("tooltip", True))
        self.assertEqual(self.types(report)["Order details"], ("drillthrough", False))
        self.assertEqual(len(report["bookmarks"]), 3)

    def test_legacy_layout_of_a_pbip(self):
        folder = self.tmp / "Sales.Report"
        folder.mkdir()
        (folder / "report.json").write_text(json.dumps(legacy_layout()), encoding="utf-8")
        report = parse_report(folder)
        self.check_legacy(report)
        self.assertEqual([(b["name"], b.get("group"), b.get("page")) for b in report["bookmarks"]],
                         [("One", None, "ReportSection3"), ("Two", "Saved views", "ReportSection1")])

    def test_legacy_layout_of_a_pbix(self):
        pbix = self.tmp / "Sales.pbix"
        with zipfile.ZipFile(pbix, "w") as archive:
            archive.writestr("Report/Layout", json.dumps(legacy_layout()).encode("utf-16-le"))
        out = self.tmp / "extract"
        portable.extract(pbix, out)
        _, report = load_extracted(out, pbix, False)
        self.check_legacy(report)
        # and read straight from the Layout file
        layout = self.tmp / "Layout"
        layout.write_bytes(json.dumps(legacy_layout()).encode("utf-16"))
        self.check_legacy(parse_legacy_layout(layout, "Sales"))

    def test_pbi_tools_extract(self):
        report_dir = self.tmp / "Report"
        (report_dir / "sections").mkdir(parents=True)
        (report_dir / "report.json").write_text("{}", encoding="utf-8")
        for index, section in enumerate(legacy_sections()):
            folder = report_dir / "sections" / f"{index:03d}_{section['name']}"
            folder.mkdir()
            # pbi-tools writes config and filters beside section.json; a page without settings has no config.json
            for key in ("config", "filters"):
                if key in section:
                    (folder / f"{key}.json").write_text(json.dumps(json.loads(section.pop(key))), encoding="utf-8")
            (folder / "section.json").write_text(json.dumps(section), encoding="utf-8")
        for name, active in (("Germany", "ReportSection3"), ("US", None)):
            (report_dir / "bookmarks" / name).mkdir(parents=True)
            head = {"displayName": name, "name": "Bookmark" + name}
            if active:
                head["explorationState"] = {"version": "1.3", "activeSection": active}
            (report_dir / "bookmarks" / name / "bookmark.json").write_text(json.dumps(head), encoding="utf-8")
        report = parse_extracted_report(report_dir, "Sales")
        self.check_legacy(report)
        self.assertEqual([(b["name"], b.get("page")) for b in report["bookmarks"]], [("Germany", "ReportSection3"), ("US", None)])


class Outputs(Base):
    def test_agent_output_names_page_types(self):
        report = parse_report(pbir_report(self.tmp))
        text = build_agent_md(build_payload(None, report, None, "Sales"))
        self.assertIn("### Page: Sales / Sales tooltip [tip] (tooltip page, hidden)", text)
        self.assertIn("### Page: Sales / Order details [detail] (drillthrough page)", text)
        self.assertRegex(text, r"### Page: Sales / Home \[home\]\n")

    @unittest.skipUnless(shutil.which("node"), "Node needed to run the generated script")
    def test_the_report_view(self):
        report = parse_report(pbir_report(self.tmp))
        payload = build_payload(None, report, None, "Sales")
        html = render_html(payload, self.tmp / "report.html")
        result = subprocess.run(["node", str(Path(__file__).with_name("check_report_view.cjs")), str(html)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
