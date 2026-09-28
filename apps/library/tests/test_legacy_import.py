"""Legacy engine HTML through POST /imports and /imports/preview (handoff section 4)."""
import json
import unittest

from test_api import MUTATE, ApiTest

from bidoc_engines import adf
from fixtures import ADF_MARKERS


class LegacyImport(ApiTest):
    def legacy_html(self) -> bytes:
        return adf.render(adf.load(self.factory, "adf_git")).encode()

    def test_preview_then_import_converts_and_withholds_code(self):
        html = self.legacy_html()
        meta = json.dumps({"title": "Old factory", "environment": "Production", "owner": "Ops"})
        pre = self.client.post("/api/v1/imports/preview", files={"file": ("old.html", html, "text/html")},
                               data={"legacy_metadata": meta}, headers=MUTATE)
        self.assertEqual(pre.status_code, 200, pre.text)
        body = pre.json()
        self.assertEqual((body["input"], body["outcome"], body["document_id"]), ("legacy", "new_document", None))
        self.assertEqual((body["title"], body["query_code"]), ("Old factory", "withheld"))
        self.assertTrue(body["omissions"])
        self.assertTrue(any("not carried over" in w for w in body["coverage_warnings"]))
        self.assertEqual(self.client.get("/api/v1/documents").json()["items"], [])        # nothing stored

        out = self.upload(html, legacy_metadata=meta)
        self.assertEqual(out.status_code, 201, out.text)
        doc = self.client.get(f"/api/v1/documents/{out.json()['document_id']}").json()
        self.assertEqual((doc["title"], doc["classification"]["environment"]), ("Old factory", "Production"))
        self.assertEqual(doc["publication"]["environment_key"], "production")
        stored = self.client.get(f"/api/v1/documents/{doc['document_id']}/revisions/{doc['current_revision_id']}"
                                 "/download").content
        for value in ADF_MARKERS.values():
            self.assertNotIn(value.encode(), stored, value)
        self.assertIn(b'"source":{"kind":"legacy"', stored.replace(b" ", b""))
        self.assertIn(b"published, read-only copy", stored)
        again = self.upload(html, legacy_metadata=meta)                                   # byte duplicate
        self.assertEqual((again.status_code, again.json()["document_id"]), (200, doc["document_id"]))

    def test_legacy_as_new_version_of_existing_document(self):
        out = self.upload(self.legacy_html()).json()
        etag = self.client.get(f"/api/v1/documents/{out['document_id']}").headers["ETag"]
        html = self.legacy_html().replace(b"<body", b"<!-- edited --><body", 1)
        second = self.upload(html, target_document_id=out["document_id"], headers={"If-Match": etag})
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["document_id"], out["document_id"])
        self.assertApiError(self.upload(html + b" ", target_document_id=out["document_id"]), 428,
                            "PRECONDITION_REQUIRED")

    def test_unknown_html_and_bad_metadata_are_refused(self):
        self.assertApiError(self.upload(b"<html><body>Hello</body></html>"), 422, "UNSUPPORTED_SAFE_PROJECTION")
        self.assertApiError(self.upload(self.legacy_html(), legacy_metadata='{"colour": 1}'), 400, "INVALID_REQUEST")
        pre = self.client.post("/api/v1/imports/preview", files={"file": ("x.html", b"<p>x</p>", "text/html")},
                               headers=MUTATE)
        self.assertApiError(pre, 422, "UNSUPPORTED_SAFE_PROJECTION")



from backends import add_variants  # noqa: E402

add_variants(globals(), (LegacyImport,))

if __name__ == "__main__":
    unittest.main()
