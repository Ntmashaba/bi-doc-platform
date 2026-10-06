"""Personal local paths in shared publication (owner decision, B13 follow-up).

Local output keeps full paths. Shared output withholds personal locations everywhere
(values, code, labels, IDs) while keeping distinct files distinct and stable, and keeps
shared locations (UNC, SharePoint, storage URLs) and relative repository paths.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from bidoc_contracts import validate_artifact
from bidoc_engines.generate import GenerateRequest, generate
from bidoc_engines.projection import project, withhold_personal_paths

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "relationships"))
from bidoc_relationships import Document, detect  # noqa: E402

SOURCES = [r'let S = Excel.Workbook(File.Contents("C:\Users\Alice\Data\Budget.xlsx"), null, true) in S',
           r'let S = Excel.Workbook(File.Contents("C:\Users\Alice\Archive\Budget.xlsx"), null, true) in S',
           r'let S = Csv.Document(File.Contents("\\fileserver\finance\rates.csv")) in S',
           r'let S = SharePoint.Files("https://contoso.sharepoint.com/sites/fin") in S']


def model(folder: Path, sources=SOURCES) -> Path:
    tables = [{"name": f"T{i}", "columns": [{"name": "A"}], "partitions": [{"name": f"T{i}", "source": {
        "type": "m", "expression": e}}]} for i, e in enumerate(sources)]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.bim").write_text(json.dumps({"model": {"name": "Paths", "tables": tables}}), encoding="utf-8")
    return folder / "model.bim"


class Text(unittest.TestCase):
    def test_replacements(self):
        cases = {
            r"C:\Users\Alice\Data\Budget.xlsx": "Budget.xlsx — personal location withheld",
            r'File.Contents("C:\Users\Alice\My Data\Budget 2024.xlsx")': 'File.Contents("Budget 2024.xlsx — ',
            r"Could not read C:\Users\Alice\Sales.pbix now": "Could not read Sales.pbix — ",
            "file:///C:/Users/Alice/x.csv": "x.csv — ",
            "/home/alice/data/x.csv": "x.csv — ",
            "/Users/bob/Desktop/report.pbix": "report.pbix — ",
            r"\\?\C:\Users\x\y.csv": "y.csv — ",
            # An Analysis Services structured data source is named after its address: kind, slash, path.
            r"File/C:\Users\Alice\Documents\Budget 2024.xlsx": "File/Budget 2024.xlsx — ",
            r"Folder/C:\Users\Alice\Data": "Folder/Data — ",
            r"Unresolved M reference: File/C:\Users\Alice\x.csv": "Unresolved M reference: File/x.csv — ",
            r'Source = #"File/C:\Users\Alice\x.csv",': 'Source = #"File/x.csv — ',
        }
        for raw, expected in cases.items():
            out = withhold_personal_paths(raw)
            self.assertIn(expected, out, raw)
            self.assertNotRegex(out, r"(?i)alice|bob|users[\\/]", raw)
            self.assertEqual(withhold_personal_paths(out), out, "idempotent")

    def test_shared_and_relative_locations_are_kept(self):
        for keep in (r"\\fileserver\finance\budget.xlsx", "https://contoso.sharepoint.com/sites/x/Budget.xlsx",
                     "abfss://raw@lake.dfs.core.windows.net/a/b.csv", "pipeline/LoadSales.json",
                     "file://server/share/x.csv", "ratio 3:1 of a/b", "SUM(Sales[Amount])", "https://x.com/home/a",
                     # a drive letter after "/" inside a URL or a longer path is not a data source name
                     "https://x.com/C:/a/b.csv", "https://host/File/C:/a/b.csv", "\\\\srv\\share\\File/C:\\a.csv",
                     "abfss://raw@lake.dfs.core.windows.net/a/C:/b.csv", "either/or:/x"):
            self.assertEqual(withhold_personal_paths(keep), keep)

    def test_distinct_files_stay_distinct_and_refs_are_stable(self):
        a = withhold_personal_paths(r"C:\Users\Alice\Data\Budget.xlsx")
        b = withhold_personal_paths(r"C:\Users\Alice\Archive\Budget.xlsx")
        self.assertNotEqual(a, b)
        self.assertEqual(a, withhold_personal_paths(r"C:\Users\Alice\Data\Budget.xlsx"))
        self.assertEqual(a, withhold_personal_paths("c:/users/alice/data/budget.xlsx").replace("budget", "Budget"))

    def test_a_path_in_a_data_source_name_has_the_reference_of_the_bare_path(self):
        bare = withhold_personal_paths(r"C:\Users\Alice\Data\Budget.xlsx")
        self.assertEqual(withhold_personal_paths(r"File/C:\Users\Alice\Data\Budget.xlsx"), "File/" + bare)

    def test_reference_is_the_full_digest(self):
        import hashlib
        out = withhold_personal_paths(r"C:\Users\Alice\Data\Budget.xlsx")
        digest = hashlib.sha256(b"c:/users/alice/data/budget.xlsx").hexdigest()
        self.assertTrue(out.endswith(f"[ref {digest}]"))

    def test_moving_the_file_changes_the_reference(self):
        self.assertNotEqual(withhold_personal_paths(r"C:\Users\Alice\Data\Budget.xlsx"),
                            withhold_personal_paths(r"C:\Users\Bob\Data\Budget.xlsx"))

    def test_projecting_again_changes_nothing(self):
        payload = {"a": r'File.Contents("C:\Users\Alice\Data\Budget.xlsx")', "b": "/home/alice/x.csv"}
        once, _ = project("power_bi", payload, query_code="included")
        twice, omissions = project("power_bi", once, query_code="included")
        self.assertEqual(once, twice)
        self.assertEqual([o for o in omissions if o["reason"] == "machine_path"], [])

    def test_projection_covers_every_field_and_records_omissions(self):
        payload = {"documentation": {"reportLocation": r"C:\Users\Alice\Reports"},
                   "pbixSource": r"C:\Users\Alice\Reports\Sales.pbix",
                   "entities": [{"key": r"file:C:\Users\Alice\in.csv", "endpoint": {"path": r"C:\Users\Alice\in.csv"}}]}
        out, omissions = project("adf", payload, query_code="included")
        self.assertNotIn("Alice", json.dumps(out))
        self.assertEqual({o["reason"] for o in omissions}, {"machine_path"})
        self.assertEqual(len(omissions), 4)


class SharedPublication(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def publish(self, profile, sources=SOURCES, out="out"):
        r = generate(GenerateRequest(engine="power_bi", source_path=str(model(self.tmp / out, sources)),
                                     source_kind="bim", output_dir=str(self.tmp / f"{out}-{profile}"),
                                     environment="Production", profile=profile))
        self.assertEqual(r.status, "completed", r.errors)
        return Path(r.artifact_path).read_text(encoding="utf-8")

    def sources(self, html):
        return {o["object_id"]: o for o in validate_artifact(html.encode())["objects"] if o["kind"] == "source"}

    def test_local_output_keeps_full_paths(self):
        self.assertIn("Alice", self.publish("local"))

    def test_shared_output_withholds_personal_paths_but_keeps_sources_distinct(self):
        html = self.publish("shared")
        self.assertNotIn("Alice", html)
        sources = self.sources(html)
        self.assertEqual(len(sources), 4)
        withheld = [o for o in sources.values() if o["bindings"][0]["endpoint"]["path"] and
                    o["bindings"][0]["endpoint"]["path"].startswith("withheld:")]
        self.assertEqual(len(withheld), 2)                                   # two Budget.xlsx files, not one
        self.assertEqual(len({o["label"] for o in withheld}), 2)
        for o in withheld:
            self.assertRegex(o["label"], r"^Budget\.xlsx — personal location withheld \(ref [0-9a-f]{8}\)$")
            self.assertRegex(o["bindings"][0]["endpoint"]["path"], r"^withheld:[0-9a-f]{64}$")
            self.assertRegex(o["object_id"], r"withheld%3A[0-9a-f]{64}")
        paths = {o["bindings"][0]["endpoint"]["path"] for o in sources.values()}
        self.assertIn(r"\\fileserver\finance\rates.csv", paths)            # shared network location kept
        self.assertTrue(any("sharepoint.com" in (o["bindings"][0]["endpoint"]["url"] or "") or
                            "sharepoint.com" in o["object_id"] for o in sources.values()))

    def test_a_structured_data_source_named_after_a_personal_path_is_withheld(self):
        # Analysis Services names a structured data source after its address, so the path is in the name, and the
        # name is repeated wherever the document says what a table was traced from.
        name = r"File/C:\Users\Alice\Documents\Budget 2024.xlsx"
        doc = {"compatibilityLevel": 1500, "model": {
            "dataSources": [{"type": "structured", "name": name, "connectionDetails": {
                "protocol": "file", "address": {"path": r"C:\Users\Alice\Documents\Budget 2024.xlsx"}}}],
            "tables": [{"name": "Budget", "columns": [{"name": "A"}], "partitions": [{"name": "Budget", "source": {
                "type": "m", "expression": f'let Source = Excel.Workbook(#"{name}", null, true) in Source'}}]}]}}
        folder = self.tmp / "named"
        folder.mkdir()
        (folder / "model.bim").write_text(json.dumps(doc), encoding="utf-8")

        def publish(profile):
            r = generate(GenerateRequest(engine="power_bi", source_path=str(folder / "model.bim"), source_kind="bim",
                                         output_dir=str(self.tmp / f"named-{profile}"), environment="Production", profile=profile))
            self.assertEqual(r.status, "completed", r.errors)
            return Path(r.artifact_path).read_text(encoding="utf-8")

        self.assertIn("Alice", publish("local"))
        shared = publish("shared")
        self.assertNotIn("Alice", shared)
        self.assertIn("File/Budget 2024.xlsx — personal location withheld", shared)

    def test_source_ids_are_stable_across_revisions(self):
        first = set(self.sources(self.publish("shared", out="a")))
        second = set(self.sources(self.publish("shared", SOURCES[:1] + SOURCES, out="b")))  # an extra table
        self.assertEqual(first, second)

    def test_short_reference_collisions_keep_identity_and_lengthen_labels(self):
        from bidoc_engines import power_bi
        a, b = "ab12cd34" + "0" * 56, "ab12cd34" + "f" * 56             # same 8-character prefix
        rows = [{"sourceKind": "file", "sourceType": "Excel workbook", "object": "Budget.xlsx", "status": "Resolved",
                 "table": f"T{i}", "location": f"Budget.xlsx — personal location withheld [ref {r}]"}
                for i, r in enumerate((a, b))]
        objects, _, _ = power_bi.describe({"title": "x", "mode": "model", "sourceObjects": rows})
        sources = [o for o in objects if o["kind"] == "source"]
        self.assertEqual(len({o["object_id"] for o in sources}), 2)
        self.assertEqual(len({o["label"] for o in sources}), 2)
        self.assertTrue(all("(ref ab12cd340000)" in o["label"] or "(ref ab12cd34ffff)" in o["label"] for o in sources))

    def test_withheld_paths_never_produce_relationships(self):
        html = self.publish("shared")
        objs = list(self.sources(html).values())
        writer = {"object_id": "adf:activity:PL/Copy", "kind": "activity", "bindings": [
            {**objs[0]["bindings"][0], "operation": "write"}]}
        docs = [Document("d1", "r1", "power_bi", "prod", tuple(objs)),
                Document("d2", "r2", "adf", "prod", (writer,))]
        self.assertEqual(detect(docs), [])


if __name__ == "__main__":
    unittest.main()
