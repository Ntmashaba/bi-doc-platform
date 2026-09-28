"""B11: publishing tokens, the token-only publishing namespace, and installer releases
(handoff 7, 8, 17.5; A11 and the local part of A36)."""
import hashlib
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

from bidoc_library.config import Settings
from bidoc_library.publishing import Publishing
from test_api import MUTATE, ApiTest

P = "/api/v1/publishing"


class Tokens(ApiTest):
    def issue(self, days=7, label="Laptop"):
        r = self.client.post("/api/v1/publish-tokens", json={"label": label, "expires_in_days": days}, headers=MUTATE)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def bearer(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_token_is_shown_once_and_stored_hashed(self):
        t = self.issue()
        self.assertRegex(t["token"], r"^bidocpt_[0-9a-f]{16}_[A-Za-z0-9_-]{43}$")
        listed = self.client.get("/api/v1/publish-tokens").json()["items"]
        self.assertEqual([x["token_id"] for x in listed], [t["token_id"]])
        self.assertNotIn("token", listed[0])
        stored = self.app.state.store.token_get(t["token_id"])
        self.assertEqual(stored["token_hash"], hashlib.sha256(t["token"].encode()).hexdigest())
        self.assertNotIn(t["token"].split("_")[2], repr(stored))
        for days in (0, 31):
            self.assertApiError(self.client.post("/api/v1/publish-tokens", json={"label": "x", "expires_in_days": days},
                                                 headers=MUTATE), 400, "INVALID_REQUEST")
        # Issuing needs the browser identity, not a publishing token.
        self.assertApiError(self.client.post("/api/v1/publish-tokens", json={"label": "x", "expires_in_days": 1},
                                             headers=self.bearer(t["token"])), 401, "UNAUTHENTICATED")

    def test_direct_publish_sequence_is_cookie_free(self):                 # A36 (local part)
        t = self.issue()
        auth = self.bearer(t["token"])
        caps = self.client.get(f"{P}/capabilities", headers=auth)
        self.assertEqual((caps.status_code, caps.json()["subject"]), (200, "local-owner"))
        data = self.artifact()
        up = lambda key, headers={}: self.client.post(  # noqa: E731
            f"{P}/imports", files={"file": ("d.html", data, "text/html")},
            headers={**auth, "Idempotency-Key": key, **headers})
        first = up("pub-1")
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(up("pub-1").json(), first.json())                  # lost response: same outcome
        out = first.json()
        doc = self.client.get(f"{P}/documents/{out['document_id']}", headers=auth)
        self.assertEqual(doc.json()["current_revision_id"], out["revision_id"])
        result = self.client.get(f"{P}/results/{out['import_id']}", headers=auth).json()
        self.assertEqual((result["revision_id"], len(result["artifact_sha256"])), (out["revision_id"], 64))
        self.assertEqual(self.client.get(f"{P}/imports/{out['import_id']}", headers=auth).json()["state"], "committed")
        second = self.artifact()
        r = self.client.post(f"{P}/imports", files={"file": ("d.html", second, "text/html")},
                             headers={**auth, "Idempotency-Key": "pub-2"})
        self.assertApiError(r, 428, "PRECONDITION_REQUIRED")
        r = self.client.post(f"{P}/imports", files={"file": ("d.html", second, "text/html")},
                             headers={**auth, "Idempotency-Key": "pub-3", "If-Match": doc.headers["ETag"]})
        self.assertEqual(r.status_code, 201, r.text)

    def test_bad_expired_and_revoked_tokens_fail_the_same_way(self):     # A11
        t = self.issue()
        wrong = t["token"][:-3] + ("aaa" if not t["token"].endswith("aaa") else "bbb")
        store = self.app.state.store
        past = datetime.now(timezone.utc) - timedelta(days=10)
        expired = Publishing(store, clock=lambda: past).issue("local-owner", "old", 1)["token"]
        for bad in (None, "Bearer", "Basic abc", f"Bearer {wrong}", f"Bearer {expired}",
                    "Bearer bidocpt_0000000000000000_" + "a" * 43):
            r = self.client.get(f"{P}/capabilities", headers={"Authorization": bad} if bad else {})
            self.assertApiError(r, 401, "UNAUTHENTICATED")
            self.assertEqual(r.json()["error"]["message"], "a valid publishing token is required")
        self.assertEqual(self.client.delete(f"/api/v1/publish-tokens/{t['token_id']}", headers=MUTATE).status_code, 204)
        self.assertApiError(self.client.get(f"{P}/capabilities", headers=self.bearer(t["token"])), 401,
                            "UNAUTHENTICATED")
        self.assertApiError(self.client.post(f"{P}/imports", files={"file": ("d.html", self.artifact(), "text/html")},
                                             headers={**self.bearer(t["token"]), "Idempotency-Key": "x"}),
                            401, "UNAUTHENTICATED")
        states = {x["token_id"]: x["state"] for x in self.client.get("/api/v1/publish-tokens").json()["items"]}
        self.assertEqual(states[t["token_id"]], "revoked")
        self.assertIn("expired", states.values())

    def test_a_token_only_reaches_the_publishing_namespace(self):
        auth = self.bearer(self.issue()["token"])
        out = self.client.post(f"{P}/imports", files={"file": ("d.html", self.artifact(), "text/html")},
                               headers={**auth, "Idempotency-Key": "k"}).json()
        doc = f"/api/v1/documents/{out['document_id']}"
        etag = self.client.get(doc).headers["ETag"]
        for method, path, extra in (("post", f"{doc}/archive", {"If-Match": etag}),
                                    ("post", "/api/v1/publish-tokens", {}),
                                    ("patch", f"{doc}/metadata", {"If-Match": etag}),
                                    ("post", "/api/v1/imports", {"Idempotency-Key": "z"})):
            r = getattr(self.client, method)(path, headers={**auth, **extra}, json={"title": "x", "reason": "r"})
            self.assertEqual(r.status_code, 401, (path, r.text))


class Gateway(ApiTest):
    settings = Settings(auth_mode="gateway", gateway_trusted_proxies=("10.0.0.0/24",))
    ann = {"X-Forwarded-User": "ann@corp", "X-Forwarded-Roles": "publisher", "X-Requested-With": "bidoc"}
    bob = {"X-Forwarded-User": "bob@corp", "X-Forwarded-Roles": "publisher", "X-Requested-With": "bidoc"}
    admin = {"X-Forwarded-User": "root@corp", "X-Forwarded-Roles": "admin", "X-Requested-With": "bidoc"}

    def via(self, ip="10.0.0.5", https=True):
        return TestClient(self.app, base_url="http://library.corp", client=(ip, 5000), raise_server_exceptions=False,
                          headers={"X-Forwarded-Proto": "https"} if https else {})

    def token(self, who):
        r = self.via().post("/api/v1/publish-tokens", json={"label": "pc", "expires_in_days": 5}, headers=who)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_https_is_required_outside_loopback(self):
        auth = {"Authorization": f"Bearer {self.token(self.ann)['token']}"}
        self.assertEqual(self.via().get(f"{P}/capabilities", headers=auth).status_code, 200)
        self.assertApiError(self.via(https=False).get(f"{P}/capabilities", headers=auth), 403, "FORBIDDEN")
        # X-Forwarded-Proto is trusted only from the configured ingress.
        self.assertApiError(self.via("192.168.1.9").get(f"{P}/capabilities", headers=auth), 403, "FORBIDDEN")

    def test_owners_and_admins_revoke_and_imports_stay_private(self):
        ann, bob = self.token(self.ann), self.token(self.bob)
        self.assertApiError(self.via().delete(f"/api/v1/publish-tokens/{ann['token_id']}", headers=self.bob),
                            404, "NOT_FOUND")
        self.assertApiError(self.via().get("/api/v1/publish-tokens?all=true", headers=self.bob), 403, "FORBIDDEN")
        self.assertEqual(len(self.via().get("/api/v1/publish-tokens?all=true", headers=self.admin).json()["items"]), 2)
        a = {"Authorization": f"Bearer {ann['token']}"}
        out = self.via().post(f"{P}/imports", files={"file": ("d.html", self.artifact(), "text/html")},
                              headers={**a, "Idempotency-Key": "k"}).json()
        b = {"Authorization": f"Bearer {bob['token']}"}
        self.assertApiError(self.via().get(f"{P}/imports/{out['import_id']}", headers=b), 404, "NOT_FOUND")
        self.assertApiError(self.via().get(f"{P}/results/{out['import_id']}", headers=b), 404, "NOT_FOUND")
        # Publisher role removed: the administrator revokes every token of that subject.
        r = self.via().post("/api/v1/publish-tokens/revoke-subject", json={"subject": "ann@corp"}, headers=self.admin)
        self.assertEqual(r.json(), {"revoked": 1})
        self.assertApiError(self.via().get(f"{P}/capabilities", headers=a), 401, "UNAUTHENTICATED")
        self.assertEqual(self.via().get(f"{P}/capabilities", headers=b).status_code, 200)
        audit = self.app.state.store.token_audit(ann["token_id"])
        self.assertEqual([x["action"] for x in audit], ["issue", "admin-revoke-subject"])


class Releases(ApiTest):
    exe = b"MZ" + b"\0" * 2048

    def upload(self, version="0.3.0", sha=None, headers=MUTATE):
        return self.client.post("/api/v1/releases", headers=headers, files={
            "file": ("bidoc-setup-0.3.0-unsigned.exe", self.exe, "application/octet-stream")}, data={
            "version": version, "sha256": sha or hashlib.sha256(self.exe).hexdigest(),
            "release_notes": "First pilot build.", "prerequisites": '["Power BI Desktop (PBIX only)"]'})

    def test_upload_approve_download(self):
        self.assertApiError(self.client.get("/api/v1/releases/latest"), 404, "NO_APPROVED_RELEASE")
        self.assertFalse(self.client.get("/api/v1/capabilities").json()["installer_available"])
        self.assertApiError(self.upload(sha="0" * 64), 422, "CHECKSUM_MISMATCH")
        self.assertApiError(self.upload(version="1.2"), 400, "INVALID_REQUEST")
        r = self.upload()
        self.assertEqual((r.status_code, r.json()["approved"]), (201, False), r.text)
        self.assertApiError(self.client.get("/api/v1/releases/latest"), 404, "NO_APPROVED_RELEASE")
        self.assertApiError(self.client.get("/api/v1/releases/0.3.0/download"), 404, "NO_APPROVED_RELEASE")
        self.assertApiError(self.upload(), 409, "RELEASE_EXISTS")
        self.assertEqual(self.client.post("/api/v1/releases/0.3.0/approve", headers=MUTATE).status_code, 200)
        latest = self.client.get("/api/v1/releases/latest").json()
        self.assertEqual((latest["version"], latest["download_url"]), ("0.3.0", "/api/v1/releases/0.3.0/download"))
        dl = self.client.get(latest["download_url"])
        self.assertEqual(dl.content, self.exe)
        self.assertTrue(dl.headers["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(dl.headers["X-Checksum-SHA256"], hashlib.sha256(self.exe).hexdigest())
        self.assertTrue(self.client.get("/api/v1/capabilities").json()["installer_available"])


class GatewayReleases(Releases):
    settings = Settings(auth_mode="gateway", gateway_trusted_proxies=("10.0.0.0/24",))

    def make_client(self, **kw):
        return TestClient(self.app, base_url="http://library.corp", client=("10.0.0.5", 5000),
                          raise_server_exceptions=False, headers={
                              "X-Forwarded-User": "root@corp", "X-Forwarded-Roles": "admin",
                              "X-Requested-With": "bidoc"}, **kw)

    def test_only_admins_upload_and_viewers_see_approved_only(self):
        viewer = {"X-Forwarded-User": "v@corp", "X-Forwarded-Roles": "viewer", "X-Requested-With": "bidoc"}
        self.assertApiError(self.upload(headers=viewer), 403, "FORBIDDEN")
        self.upload()
        self.assertEqual(len(self.client.get("/api/v1/releases").json()["items"]), 1)            # admin
        self.assertEqual(self.client.get("/api/v1/releases", headers=viewer).json()["items"], [])
        self.assertApiError(self.client.post("/api/v1/releases/0.3.0/approve", headers=viewer), 403, "FORBIDDEN")


from backends import add_variants  # noqa: E402

add_variants(globals(), (Tokens, Gateway, Releases, GatewayReleases))

if __name__ == "__main__":
    unittest.main()
