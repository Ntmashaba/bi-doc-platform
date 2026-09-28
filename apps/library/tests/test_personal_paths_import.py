"""Personal paths through the library: re-import keeps references; a moved file needs review."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_derived import MUTATE, DerivedTest  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402

BUDGET = r'let S = Excel.Workbook(File.Contents("{}"), null, true) in S'


class PersonalPaths(DerivedTest):
    def budget_model(self, budget_path):
        folder = self.tmp / "paths"
        folder.mkdir(exist_ok=True)
        (folder / "model.bim").write_text(json.dumps({"model": {"name": "Budgets", "tables": [
            {"name": "Budget", "columns": [{"name": "A"}], "partitions": [{"name": "Budget", "source": {
                "type": "m", "expression": BUDGET.format(budget_path)}}]}]}}), encoding="utf-8")
        return folder / "model.bim"

    def source_ids(self, doc_id):
        items = self.c.get(f"/api/v1/documents/{doc_id}/objects").json()["items"]
        return [o["object_id"] for o in items if o["kind"] == "source"]

    def test_reimport_keeps_the_reference(self):
        path = self.budget_model(r"C:\Users\Alice\Data\Budget.xlsx")
        data = self.artifact("power_bi", path, "bim", profile="shared")
        manifest_ids = [o["object_id"] for o in validate_artifact(data)["objects"] if o["kind"] == "source"]
        self.assertRegex(manifest_ids[0], r"withheld%3A[0-9a-f]{64}$")
        out = self.publish(data)                          # the library projects the redacted payload again
        self.assertEqual(self.source_ids(out["document_id"]), manifest_ids)
        # a local artifact of the same model (full paths) gets the same references in the library
        local = self.artifact("power_bi", path, "bim")
        self.assertIn(b"Alice", local)
        self.publish(local, etag=self.etag(out["document_id"]))
        self.assertEqual(self.source_ids(out["document_id"]), manifest_ids)
        stored = self.c.get(f"/api/v1/documents/{out['document_id']}/revisions/{out['revision_id']}/download")
        self.assertNotIn(b"Alice", stored.content)
        self.assertIn(b"personal location withheld", stored.content)

    def test_moved_file_gets_a_new_reference_and_links_need_review(self):
        adf = self.publish(self.artifact("adf", self.factory, "adf_git"))
        pbi = self.publish(self.artifact("power_bi", self.budget_model(r"C:\Users\Alice\Data\Budget.xlsx"), "bim"))
        [old] = self.source_ids(pbi["document_id"])
        body = {"source_document_id": adf["document_id"], "source_revision_id": adf["revision_id"],
                "source_object_id": "adf:pipeline:PL_Load", "target_document_id": pbi["document_id"],
                "target_revision_id": pbi["revision_id"], "target_object_id": old,
                "expected_catalogue_sequence": self.c.get("/api/v1/search-index").json()["generation"],
                "kind": "related_to", "reason": "The load prepares this budget."}
        r = self.c.post("/api/v1/relationships/manual", json=body, headers=MUTATE)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.rel(pbi["document_id"])["manual"][0]["status"], "active")
        again = self.publish(self.artifact("power_bi", self.budget_model(r"C:\Users\Alice\Data\Budget.xlsx"), "bim"),
                             etag=self.etag(pbi["document_id"]))
        self.assertEqual(self.source_ids(pbi["document_id"]), [old])          # same path: same reference
        self.assertNotEqual(again["revision_id"], pbi["revision_id"])
        self.assertEqual(self.rel(pbi["document_id"])["manual"][0]["status"], "active")
        self.publish(self.artifact("power_bi", self.budget_model(r"C:\Users\Alice\Archive\Budget.xlsx"), "bim"),
                     etag=self.etag(pbi["document_id"]))
        [new] = self.source_ids(pbi["document_id"])
        self.assertNotEqual(new, old)                                         # moved: new reference
        self.assertEqual(self.rel(pbi["document_id"])["manual"][0]["status"], "needs_review")


from backends import add_variants  # noqa: E402

add_variants(globals(), (PersonalPaths,))

if __name__ == "__main__":
    unittest.main()
