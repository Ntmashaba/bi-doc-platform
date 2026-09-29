"""PBIR pages are identified by the `name` in page.json, which need not equal the folder name."""
import json
import tempfile
import unittest
from pathlib import Path

from pbidocgen.report_parser import parse_report


def write_report(root: Path, declared: list[str], active: str, pages: dict[str, str]) -> Path:
    """`pages` maps a page folder name to the page's declared `name`."""
    report = root / "Sales.Report"
    definition = report / "definition"
    (definition / "pages").mkdir(parents=True)
    (definition / "report.json").write_text('{"name":"Sales"}')
    (definition / "pages" / "pages.json").write_text(json.dumps({"pageOrder": declared, "activePageName": active}))
    for folder, name in pages.items():
        page = definition / "pages" / folder
        page.mkdir()
        (page / "page.json").write_text(json.dumps({"name": name, "displayName": f"Page {name}"}))
    return report


def missing_warnings(parsed) -> list[str]:
    return [w["message"] for w in parsed["warnings"] if "is missing from the extract" in w["message"]]


class PbirPageIdTests(unittest.TestCase):
    def test_pages_are_matched_by_declared_name_not_folder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = write_report(Path(tmp), ["second", "first"], "second",
                                  {"ReportSection1": "first", "ReportSection2": "second"})
            parsed = parse_report(report)
        self.assertEqual(missing_warnings(parsed), [])
        self.assertEqual([p["id"] for p in parsed["pages"]], ["second", "first"])
        self.assertEqual([p["isActive"] for p in parsed["pages"]], [True, False])

    def test_a_declared_page_with_no_folder_is_still_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = write_report(Path(tmp), ["first", "gone"], "first", {"ReportSection1": "first"})
            parsed = parse_report(report)
        self.assertEqual(len(missing_warnings(parsed)), 1)
        self.assertIn("'gone'", missing_warnings(parsed)[0])

    def test_folder_name_is_used_when_page_json_has_no_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = write_report(Path(tmp), ["p1"], "p1", {})
            page = report / "definition" / "pages" / "p1"
            page.mkdir()
            (page / "page.json").write_text('{"displayName":"Summary"}')
            parsed = parse_report(report)
        self.assertEqual(missing_warnings(parsed), [])
        self.assertEqual(parsed["pages"][0]["id"], "p1")


if __name__ == "__main__":
    unittest.main()
