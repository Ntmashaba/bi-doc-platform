"""HTTP API v1: contract, status codes, access modes and error envelope (handoff 7, 8, 17.1)."""
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_library.api import create_app  # noqa: E402
from bidoc_library.config import ConfigError, Settings, from_env  # noqa: E402
from bidoc_library.openapi import generate as openapi  # noqa: E402

SECRET = "test-session-secret"
MUTATE = {"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc"}


class ApiTest(unittest.TestCase):
    settings = Settings()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.factory = adf_factory(self.tmp / "factory")
        self.app = create_app(replace(self.settings, local_data_dir=self.tmp / "data"), store=self.make_store(),
                              session_secret=SECRET)
        self.client = self.make_client()
        self.n = 0

    def make_store(self):
        return None                      # the configured backend: LocalStore

    def make_client(self, **kw):
        return TestClient(self.app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False, **kw)

    def artifact(self, source=None, **kw) -> bytes:
        r = generate(GenerateRequest(engine="adf", source_path=str(source or self.factory), source_kind="adf_git",
                                     output_dir=str(self.tmp / "out"), **kw))
        return Path(r.artifact_path).read_bytes()

    def upload(self, data, key=None, headers=None, client=None, **form):
        self.n += 1
        return (client or self.client).post(
            "/api/v1/imports", files={"file": ("doc.html", data, "text/html")}, data=form,
            headers={**MUTATE, "Idempotency-Key": key or f"k{self.n}", **(headers or {})})

    def assertApiError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()["error"]
        self.assertEqual(body["code"], code)
        self.assertEqual(body["request_id"], response.headers["X-Request-ID"])
        return body


class Health(ApiTest):
    def test_live_ready_and_capabilities(self):
        self.assertEqual(self.client.get("/api/v1/health/live").json(), {"status": "live"})
        self.assertEqual(self.client.get("/api/v1/health/ready").status_code, 200)
        caps = self.client.get("/api/v1/capabilities").json()
        self.assertEqual((caps["manifest_versions"], caps["access_mode"], caps["can_publish"]), ([1], "local", True))
        self.assertFalse(any(p["available"] for p in caps["processing"]))

    def test_not_ready_reveals_nothing(self):
        def broken():
            raise RuntimeError("/very/secret/path.sqlite3")
        self.app.state.store.catalogue_sequence = broken
        body = self.assertApiError(self.client.get("/api/v1/health/ready"), 503, "NOT_READY")
        self.assertNotIn("secret", json.dumps(body))

    def test_unexpected_errors_do_not_leak(self):
        def broken(**kw):
            raise RuntimeError("/very/secret/path.sqlite3")
        self.app.state.store.list_documents = broken
        response = self.client.get("/api/v1/documents")
        self.assertApiError(response, 500, "INTERNAL_ERROR")
        self.assertNotIn("secret", response.text)

    def test_security_headers(self):
        r = self.client.get("/api/v1/documents")
        self.assertEqual((r.headers["X-Content-Type-Options"], r.headers["Cache-Control"]), ("nosniff", "no-store"))


class Documents(ApiTest):
    def test_import_read_download_and_view(self):
        r = self.upload(self.artifact())
        self.assertEqual(r.status_code, 201, r.text)
        out = r.json()
        self.assertEqual((out["status"], out["duplicate"], out["indexing_state"]), ("completed", False, "ready"))
        doc = self.client.get(f"/api/v1/documents/{out['document_id']}")
        self.assertEqual(doc.headers["ETag"], f'"{doc.json()["etag"]}"')
        self.assertEqual(doc.json()["current_revision_id"], out["revision_id"])
        revs = self.client.get(f"/api/v1/documents/{out['document_id']}/revisions").json()["items"]
        self.assertEqual([x["revision_id"] for x in revs], [out["revision_id"]])
        base = f"/api/v1/documents/{out['document_id']}/revisions/{out['revision_id']}"
        dl = self.client.get(f"{base}/download")
        self.assertTrue(dl.headers["Content-Disposition"].startswith("attachment;"))
        self.assertIn("sandbox", dl.headers["Content-Security-Policy"])
        validate_artifact(dl.content)
        view = self.client.get(f"{base}/view")
        csp = view.headers["Content-Security-Policy"]
        for rule in ("sandbox allow-scripts", "connect-src 'none'", "form-action 'none'", "default-src 'none'"):
            self.assertIn(rule, csp)
        self.assertNotIn("allow-same-origin", csp)
        self.assertEqual(self.client.get(f"/api/v1/imports/{out['import_id']}").json()["state"], "committed")
        self.assertEqual(self.client.get("/api/v1/documents", params={"document_type": "adf"}).json()["items"][0]
                         ["document_id"], out["document_id"])

    def test_update_needs_etag(self):
        out = self.upload(self.artifact()).json()
        second = self.artifact()
        self.assertApiError(self.upload(second), 428, "PRECONDITION_REQUIRED")
        self.assertApiError(self.upload(second, headers={"If-Match": '"stale"'}), 409, "REVISION_CONFLICT")
        self.assertApiError(self.upload(second, headers={"If-Match": "*"}), 400, "INVALID_REQUEST")
        etag = self.client.get(f"/api/v1/documents/{out['document_id']}").headers["ETag"]
        self.assertEqual(self.upload(second, headers={"If-Match": etag}).status_code, 201)
        self.assertEqual(len(self.client.get(f"/api/v1/documents/{out['document_id']}/revisions").json()["items"]), 2)

    def test_idempotency_and_duplicates(self):
        data = self.artifact()
        first = self.upload(data, key="once")
        retry = self.upload(data, key="once")
        self.assertEqual(retry.json()["import_id"], first.json()["import_id"])
        dup = self.upload(data)
        self.assertEqual((dup.status_code, dup.json()["duplicate"]), (200, True))
        self.assertApiError(self.upload(self.artifact(), key="once"), 409, "IDEMPOTENCY_KEY_REUSED")

    def test_malformed_and_invalid_uploads(self):
        r = self.client.post("/api/v1/imports", files={"file": ("d.html", b"x", "text/html")}, headers=MUTATE)
        self.assertApiError(r, 400, "INVALID_REQUEST")                        # no Idempotency-Key
        self.assertApiError(self.upload(b"<html>not an artifact</html>"), 422, "UNSUPPORTED_SAFE_PROJECTION")
        self.assertApiError(self.upload(self.artifact(), target_document_id="not-a-uuid"), 400, "INVALID_REQUEST")
        self.assertApiError(self.client.get("/api/v1/documents/not-a-uuid"), 400, "INVALID_REQUEST")
        self.assertApiError(self.client.get("/api/v1/documents/00000000-0000-4000-8000-000000000000"), 404,
                            "NOT_FOUND")

    def test_upload_limit(self):
        small = create_app(replace(self.settings, local_data_dir=self.tmp / "small", max_html_bytes=1000),
                           session_secret=SECRET)
        client = TestClient(small, base_url="http://127.0.0.1:8765")
        self.assertApiError(self.upload(self.artifact(), client=client), 413, "PAYLOAD_TOO_LARGE")

    def test_archive_and_restore(self):
        out = self.upload(self.artifact()).json()
        path = f"/api/v1/documents/{out['document_id']}"
        self.assertApiError(self.client.post(f"{path}/archive", headers=MUTATE), 428, "PRECONDITION_REQUIRED")
        etag = self.client.get(path).headers["ETag"]
        archived = self.client.post(f"{path}/archive", headers={**MUTATE, "If-Match": etag})
        self.assertTrue(archived.json()["archived"])
        self.assertEqual(self.client.get("/api/v1/documents").json()["items"], [])
        self.assertEqual(len(self.client.get("/api/v1/documents", params={"archived": True}).json()["items"]), 1)
        self.assertApiError(self.client.post(f"{path}/restore", headers={**MUTATE, "If-Match": etag}), 409,
                            "REVISION_CONFLICT")
        restored = self.client.post(f"{path}/restore", headers={**MUTATE, "If-Match": archived.headers["ETag"]})
        self.assertFalse(restored.json()["archived"])

    def test_releases_are_honestly_unavailable(self):
        self.assertApiError(self.client.get("/api/v1/releases/latest"), 404, "NO_APPROVED_RELEASE")


class LocalAccess(ApiTest):
    def test_host_and_origin(self):
        other = TestClient(self.app, base_url="http://evil.example:8765")
        self.assertApiError(other.get("/api/v1/documents"), 403, "FORBIDDEN")         # DNS rebinding
        self.assertApiError(self.client.get("/api/v1/documents", headers={"Origin": "http://evil.example"}), 403,
                            "FORBIDDEN")
        self.assertEqual(self.client.get("/api/v1/documents", headers={"Origin": "http://127.0.0.1:8765"})
                         .status_code, 200)

    def test_mutations_need_session_secret_and_csrf_header(self):
        data = self.artifact()
        r = self.client.post("/api/v1/imports", files={"file": ("d.html", data, "text/html")},
                             headers={"Idempotency-Key": "a", "X-Requested-With": "bidoc"})
        self.assertApiError(r, 401, "UNAUTHENTICATED")
        r = self.client.post("/api/v1/imports", files={"file": ("d.html", data, "text/html")},
                             headers={"Idempotency-Key": "b", "X-Bidoc-Session": "wrong", "X-Requested-With": "bidoc"})
        self.assertApiError(r, 401, "UNAUTHENTICATED")
        r = self.client.post("/api/v1/imports", files={"file": ("d.html", data, "text/html")},
                             headers={"Idempotency-Key": "c", "X-Bidoc-Session": SECRET})
        self.assertApiError(r, 403, "FORBIDDEN")
        self.assertEqual(self.client.get("/api/v1/documents").status_code, 200)       # reads need no secret


class GatewayAccess(ApiTest):
    settings = Settings(auth_mode="gateway", gateway_trusted_proxies=("10.0.0.0/24",))

    def gw(self, ip="10.0.0.5"):
        return TestClient(self.app, base_url="http://library.corp", client=(ip, 5000), raise_server_exceptions=False)

    def test_identity_only_from_the_gateway(self):
        viewer = {"X-Forwarded-User": "ann@corp", "X-Forwarded-Roles": "viewer"}
        self.assertApiError(self.gw("192.168.1.9").get("/api/v1/documents", headers=viewer), 401, "UNAUTHENTICATED")
        self.assertApiError(self.gw().get("/api/v1/documents"), 401, "UNAUTHENTICATED")
        self.assertEqual(self.gw().get("/api/v1/documents", headers=viewer).status_code, 200)

    def test_roles(self):
        viewer = {"X-Forwarded-User": "ann@corp", "X-Forwarded-Roles": "viewer", "X-Requested-With": "bidoc"}
        publisher = {**viewer, "X-Forwarded-User": "bob@corp", "X-Forwarded-Roles": "publisher"}
        client = self.gw()
        self.assertApiError(self.upload(self.artifact(), client=client, headers=viewer), 403, "FORBIDDEN")
        self.assertFalse(client.get("/api/v1/capabilities", headers=viewer).json()["can_publish"])
        out = self.upload(self.artifact(), client=client, headers=publisher).json()
        revs = client.get(f"/api/v1/documents/{out['document_id']}/revisions", headers=viewer).json()["items"]
        self.assertEqual(revs[0]["publisher_subject"], "bob@corp")
        no_csrf = {k: v for k, v in publisher.items() if k != "X-Requested-With"}
        r = client.post("/api/v1/imports", files={"file": ("d.html", self.artifact(adf_factory(self.tmp / "b")),
                                                             "text/html")},
                        headers={**no_csrf, "Idempotency-Key": "no-csrf"})
        self.assertApiError(r, 403, "FORBIDDEN")
        etag = client.get(f"/api/v1/documents/{out['document_id']}", headers=publisher).headers["ETag"]
        client.post(f"/api/v1/documents/{out['document_id']}/archive", headers={**publisher, "If-Match": etag})
        self.assertApiError(client.get(f"/api/v1/documents/{out['document_id']}", headers=viewer), 404, "NOT_FOUND")
        self.assertApiError(client.get("/api/v1/documents", params={"archived": True}, headers=viewer), 403,
                            "FORBIDDEN")


class Configuration(unittest.TestCase):
    def test_unsafe_configurations_are_refused(self):
        for env in ({"AUTH_MODE": "entra"}, {"BIND_HOST": "0.0.0.0"}, {"AUTH_MODE": "gateway"},
                    {"AUTH_MODE": "none"}, {"DATA_BACKEND": "azure"}, {"PORT": "99999"}, {"PORT": "x"}):
            with self.assertRaises(ConfigError, msg=env):
                from_env(env)
        s = from_env({"AUTH_MODE": "gateway", "BIND_HOST": "0.0.0.0", "GATEWAY_TRUSTED_PROXIES": "10.0.0.1, 10.1.0.0/16"})
        self.assertEqual(s.gateway_trusted_proxies, ("10.0.0.1", "10.1.0.0/16"))

    def test_container_bind_needs_the_exact_acknowledgement(self):
        for value in ("1", "yes", "true"):
            with self.assertRaises(ConfigError):
                from_env({"BIND_HOST": "0.0.0.0", "LOCAL_CONTAINER_BIND": value})
        s = from_env({"BIND_HOST": "0.0.0.0", "LOCAL_CONTAINER_BIND": "published-on-host-loopback-only"})
        self.assertEqual((s.auth_mode, s.bind_host, s.local_container_bind), ("local", "0.0.0.0", True))

    def test_committed_openapi_is_current(self):
        committed = (ROOT / "docs" / "openapi-v1.json").read_text(encoding="utf-8")
        self.assertEqual(committed, openapi(), "regenerate: python -m bidoc_library.openapi > docs/openapi-v1.json")



from backends import add_variants  # noqa: E402

add_variants(globals(), (Health, Documents, LocalAccess, GatewayAccess))

if __name__ == "__main__":
    unittest.main()
