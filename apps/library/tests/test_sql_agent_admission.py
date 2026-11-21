"""SQL Agent admission: admitted, re-projected and re-rendered with step commands withheld (v4 5.5, 5.6).

The Phase 6 gate: a shared SQL Agent document arrives with `command` withheld and the trusted
renderer still renders a complete document from the structured fields alone.
"""
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import SQL_AGENT_MARKERS, sql_agent_mixed  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_library.api import create_app  # noqa: E402
from bidoc_library.config import Settings  # noqa: E402
from bidoc_library.store import LocalStore  # noqa: E402

SECRET = "test-session-secret"
MUTATE = {"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc"}


class Admission(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = LocalStore(self.tmp / "data")
        self.script = sql_agent_mixed(self.tmp / "mixed.sql")
        self.keys = iter(range(10_000))

    def generate(self, source=None, **kw) -> bytes:
        r = generate(GenerateRequest(engine="sql_agent", source_path=str(source or self.script),
                                     source_kind="agent_script", output_dir=str(self.tmp / "out"), **kw))
        assert r.status == "completed", r.errors
        return Path(r.artifact_path).read_bytes()

    def publish(self, data, **kw):
        return self.store.publish(data, subject="alice", idempotency_key=f"k{next(self.keys)}", **kw)

    def test_admission_reprojects_and_rerenders_without_the_commands(self):
        local = self.generate()
        manifest = validate_artifact(local)
        for name, marker in SQL_AGENT_MARKERS.items():
            self.assertIn(marker, json.dumps(manifest["native_payload"]["data"]), name)
        out = self.publish(local)
        self.assertEqual((out["state"], out["duplicate"]), ("committed", False))
        self.assertEqual(self.store.get_document(out["document_id"])["document_type"], "sql_agent")
        stored = self.store.read_artifact(out["document_id"], out["revision_id"])
        kept = validate_artifact(stored)
        self.assertEqual(kept["projection"]["profile"], "shared")
        self.assertEqual(kept["projection"]["options"], {"query_code": "withheld"})
        for name, marker in SQL_AGENT_MARKERS.items():
            self.assertNotIn(marker.encode(), stored, name)
            self.assertNotIn(marker.lower().encode(), stored, name)
        # the gate: a complete document from structured fields alone
        for structured in (b"SeededDW", b"SEEDED-SSAS-01", b"usp_SeededLoad", b"Nightly.dtsx"):
            self.assertIn(structured, stored)
        self.assertIn(b"personal location withheld", stored)
        self.assertGreater(len(kept["sections"]), 0)

    def test_query_code_can_be_included_but_never_restored(self):
        included = self.store.read_artifact(**{k: v for k, v in self.publish(self.generate(), query_code="included").items()
                                               if k in ("document_id", "revision_id")})
        self.assertIn(SQL_AGENT_MARKERS["tsql"].encode(), included)
        other = sql_agent_mixed(self.tmp / "other" / "mixed.sql")
        shared = self.generate(other, profile="shared")
        out = self.publish(shared, query_code="included")
        stored = validate_artifact(self.store.read_artifact(out["document_id"], out["revision_id"]))
        self.assertEqual(stored["projection"]["options"], {"query_code": "withheld"})

    def test_api_admits_filters_and_serves_a_sql_agent_document(self):
        app = create_app(replace(Settings(), local_data_dir=self.tmp / "api-data"), store=None,
                         session_secret=SECRET)
        client = TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False,
                            client=("127.0.0.1", 50000))
        r = client.post("/api/v1/imports", files={"file": ("doc.html", self.generate(), "text/html")},
                        headers={**MUTATE, "Idempotency-Key": "sql1"})
        self.assertEqual(r.status_code, 201, r.text)
        out = r.json()
        doc = client.get(f"/api/v1/documents/{out['document_id']}").json()
        self.assertEqual(doc["document_type"], "sql_agent")
        listed = client.get("/api/v1/documents", params={"document_type": "sql_agent"}).json()["items"]
        self.assertEqual([d["document_id"] for d in listed], [out["document_id"]])
        base = f"/api/v1/documents/{out['document_id']}/revisions/{out['revision_id']}"
        dl = client.get(f"{base}/download")
        m = validate_artifact(dl.content)
        self.assertEqual(m["document_type"], "sql_agent")
        for name, marker in SQL_AGENT_MARKERS.items():
            self.assertNotIn(marker.encode(), dl.content, name)
        view = client.get(f"{base}/view")
        self.assertEqual(view.status_code, 200)
        self.assertNotIn("allow-same-origin", view.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
