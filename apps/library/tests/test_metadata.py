"""A40: catalogue metadata overrides keep artifact bytes, provenance and stream identity."""
import hashlib
import json
import sqlite3
import unittest

from test_api import MUTATE, ApiTest


class MetadataOverrides(ApiTest):
    def publish(self, **kw):
        out = self.upload(self.artifact(**kw)).json()
        self.doc = f"/api/v1/documents/{out['document_id']}"
        return out

    def patch(self, body, etag=None):
        etag = etag or self.client.get(f"{self.doc}/metadata").headers["ETag"]
        return self.client.patch(f"{self.doc}/metadata", json=body, headers={**MUTATE, "If-Match": etag})

    def test_override_keeps_bytes_and_records_provenance(self):
        out = self.publish(title="Nightly loads", tags=("ops",))
        base = f"{self.doc}/revisions/{out['revision_id']}/download"
        self.assertEqual(self.client.get(base).status_code, 200)
        before = hashlib.sha256(self.client.get(base).content).hexdigest()
        seq = self.client.get(f"{self.doc}/metadata").json()["catalogue_sequence"]
        r = self.patch({"title": "Finance nightly loads", "owner": "Data team", "tags": ["finance", "nightly"],
                        "reason": "Catalogue clean-up"})
        self.assertEqual(r.status_code, 200, r.text)
        meta = r.json()
        self.assertEqual(meta["effective"]["title"], "Finance nightly loads")
        self.assertEqual(meta["revision"]["title"], "Nightly loads")
        self.assertEqual(meta["overrides"]["owner"]["reason"], "Catalogue clean-up")
        self.assertEqual(meta["catalogue_sequence"], seq + 1)
        self.assertEqual(r.headers["ETag"], f'"{meta["etag"]}"')
        self.assertEqual(hashlib.sha256(self.client.get(base).content).hexdigest(), before)   # bytes unchanged
        doc = self.client.get(self.doc).json()
        self.assertEqual((doc["title"], doc["classification"]["owner"], doc["tags"]),
                         ("Finance nightly loads", "Data team", ["finance", "nightly"]))
        self.assertEqual(self.client.get("/api/v1/documents", params={"tag": "finance"}).json()["items"][0]["title"],
                         "Finance nightly loads")
        hits = self.client.get("/api/v1/search", params={"q": "finance nightly"}).json()["items"]
        self.assertTrue(hits and all(h["document_id"] == out["document_id"] for h in hits))
        history = self.client.get(f"{self.doc}/metadata/history").json()
        self.assertEqual(history[0]["before"]["title"], "Nightly loads")
        self.assertEqual(history[0]["after"]["title"], "Finance nightly loads")

    def test_null_removes_override_and_new_revision_keeps_overrides(self):
        self.publish(title="Nightly loads")
        self.patch({"title": "Renamed", "reason": "x"})
        etag = self.client.get(self.doc).headers["ETag"]
        second = self.upload(self.artifact(title="Nightly loads v2"), headers={"If-Match": etag})
        self.assertEqual(second.status_code, 201, second.text)
        meta = self.client.get(f"{self.doc}/metadata").json()
        self.assertEqual((meta["effective"]["title"], meta["revision"]["title"]), ("Renamed", "Nightly loads v2"))
        meta = self.patch({"title": None, "reason": "use the artifact title"}).json()
        self.assertEqual(meta["effective"]["title"], "Nightly loads v2")
        self.assertEqual(meta["overrides"], {})

    def test_stream_identity_and_validation(self):
        self.publish()
        for field in ("environment", "environment_key", "scope_key", "asset_id"):
            self.assertApiError(self.patch({field: "x", "reason": "r"}), 422, "IMMUTABLE_FIELD")
        self.assertApiError(self.patch({"title": " padded", "reason": "r"}), 400, "INVALID_REQUEST")
        self.assertApiError(self.patch({"title": "", "reason": "r"}), 400, "INVALID_REQUEST")
        self.assertApiError(self.patch({"tags": ["a", "a"], "reason": "r"}), 400, "INVALID_REQUEST")
        self.assertApiError(self.patch({"colour": "red", "reason": "r"}), 400, "INVALID_REQUEST")
        self.assertApiError(self.patch({"reason": "nothing"}), 400, "INVALID_REQUEST")
        self.assertEqual(self.patch({"title": "t"}).status_code, 400)                 # reason is required
        self.assertEqual(self.patch({"description": "", "business_area": "", "reason": "r"}).status_code, 200)

    def test_preconditions_and_roles(self):
        self.publish()
        self.assertApiError(self.client.patch(f"{self.doc}/metadata", json={"title": "t", "reason": "r"},
                                              headers=MUTATE), 428, "PRECONDITION_REQUIRED")
        self.assertApiError(self.patch({"title": "t", "reason": "r"}, etag='"stale"'), 409, "REVISION_CONFLICT")

    def test_migration_backfills_revision_metadata(self):
        from bidoc_library import migrations
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.row_factory = sqlite3.Row
        full = migrations.MIGRATIONS
        try:
            migrations.MIGRATIONS = full[:2]
            migrations.migrate(conn, "2026-01-01T00:00:00Z")
        finally:
            migrations.MIGRATIONS = full
        conn.execute("INSERT INTO documents (document_id, document_type, asset_id, environment_key, scope_key, "
                     "business_area, environment, owner, title, description, tags, current_revision_id, created_at, "
                     "updated_at, etag) VALUES ('d','adf','a','production','factory','Fin','Production','Ops','T',"
                     "'D','[\"x\"]','r','t','t','e')")
        self.assertEqual(migrations.migrate(conn, "2026-01-02T00:00:00Z"), [3])
        self.assertEqual(json.loads(conn.execute("SELECT revision_metadata FROM documents").fetchone()[0]),
                         {"title": "T", "description": "D", "tags": ["x"], "business_area": "Fin", "owner": "Ops"})


from backends import add_variants  # noqa: E402

add_variants(globals(), (MetadataOverrides,))

if __name__ == "__main__":
    unittest.main()
