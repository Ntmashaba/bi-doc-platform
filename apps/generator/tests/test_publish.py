"""B11: direct publishing from the generator to a running library over loopback."""
import http.server
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_generator.cli import main  # noqa: E402
from bidoc_generator.publisher import LibraryClient, PublishError, normalize_url  # noqa: E402

SECRET = "publish-test-secret"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class Library:
    """A real library process (uvicorn thread) in local mode on loopback."""

    def __init__(self, data_dir):
        import uvicorn
        from bidoc_library.api import create_app
        from bidoc_library.config import Settings
        self.port = free_port()
        self.app = create_app(Settings(local_data_dir=data_dir, port=self.port), session_secret=SECRET)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}"

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.url + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc",
                                              "Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"null")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(10)


class Publish(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
        cls.tmp = Path(tempfile.mkdtemp())
        cls.library = Library(cls.tmp / "library")

    @classmethod
    def tearDownClass(cls):
        cls.library.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        os.environ["BIDOC_HOME"] = str(self.home)
        self.addCleanup(os.environ.pop, "BIDOC_HOME", None)
        self.factory = adf_factory(self.home / "factory")

    def artifact(self):
        r = generate(GenerateRequest(engine="adf", source_path=str(self.factory), source_kind="adf_git",
                                     output_dir=str(self.home / "out"), profile="shared"))
        return Path(r.artifact_path)

    def token(self):
        return self.library.call("POST", "/api/v1/publish-tokens", {"label": "test", "expires_in_days": 1})

    def test_connect_publish_retry_new_version_and_revocation(self):
        t = self.token()
        code, out, err = run(["connect", self.library.url, "--token-stdin", "--json"], stdin=t["token"] + "\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["subject"], "local-owner")
        config = (self.home / "config.json").read_text()
        self.assertIn(self.library.url, config)
        self.assertNotIn(t["token"], config)
        if os.name != "nt":
            self.assertEqual(oct((self.home / "credentials.json").stat().st_mode & 0o777), "0o600")

        first = self.artifact()
        code, out, err = run(["publish", str(first), "--json"])
        self.assertEqual(code, 0, err)
        r1 = json.loads(out)[0]
        self.assertEqual((r1["status"], r1["new_document"]), ("published", True))
        code, out, _ = run(["publish", str(first), "--json"])               # same file again: recovered outcome
        self.assertEqual((code, json.loads(out)[0]["revision_id"]), (0, r1["revision_id"]))

        second = self.artifact()                                            # regenerated: new revision
        code, out, err = run(["publish", str(second), "--json"])
        r2 = json.loads(out)[0]
        self.assertEqual((code, r2["status"], r2["new_document"], r2["document_id"]),
                         (0, "published", False, r1["document_id"]))
        doc = self.library.call("GET", f"/api/v1/documents/{r1['document_id']}")
        self.assertEqual(doc["current_revision_id"], r2["revision_id"])

        for stream in (out, err):
            self.assertNotIn(t["token"], stream)
        self.library.call("DELETE", f"/api/v1/publish-tokens/{t['token_id']}")
        code, _, err = run(["publish", str(self.artifact())])
        self.assertEqual(code, 3)
        self.assertIn("did not accept the publishing token", err)
        self.assertEqual(run(["disconnect"])[0], 0)
        self.assertEqual(run(["publish", str(first)])[0], 3)

    def test_library_limit_is_checked_before_upload(self):
        from bidoc_generator.publisher import LibraryClient, PublishError
        data = self.artifact().read_bytes()
        client = LibraryClient(self.library.url, "bidocpt_x")
        with mock.patch.object(LibraryClient, "capabilities", return_value={"limits": {"html_bytes": len(data) - 1}}):
            with self.assertRaises(PublishError) as ctx:
                client.publish(data)
        self.assertEqual(ctx.exception.code, "ARTIFACT_TOO_LARGE")
        for part in ("MiB", "not published", "usable locally"):
            self.assertIn(part, str(ctx.exception))
        sentinel = RuntimeError("past validation")
        with mock.patch.object(LibraryClient, "capabilities", return_value={"limits": {"html_bytes": len(data)}}), \
                mock.patch.object(LibraryClient, "document", side_effect=sentinel):
            with self.assertRaises(RuntimeError):          # exactly at the limit is accepted by validation
                client.publish(data)

    def test_connect_refuses_a_bad_token_and_keeps_nothing(self):
        code, _, err = run(["connect", self.library.url, "--token-stdin"], stdin="bidocpt_" + "0" * 16 + "_" + "a" * 43)
        self.assertEqual(code, 3, err)
        self.assertFalse((self.home / "config.json").exists())

    def test_partial_failure_exits_5(self):
        run(["connect", self.library.url, "--token-stdin"], stdin=self.token()["token"])
        bad = self.home / "not-a-document.html"
        bad.write_text("<html></html>")
        code, out, _ = run(["publish", str(self.artifact()), str(bad), "--json"])
        self.assertEqual(code, 5)
        self.assertEqual([r["status"] for r in json.loads(out)], ["published", "failed"])


class DesktopPublish(Publish):
    """The desktop app's Library screen and Publish action against the same real library."""

    def test_connect_and_publish_a_completed_item(self):
        from starlette.testclient import TestClient
        from bidoc_generator.batch import Options, Runner
        from bidoc_generator.desktop.app import create_app
        from bidoc_generator.history import History
        runner = Runner(History(self.home / "desktop"))
        self.addCleanup(runner.shutdown)
        app = create_app(runner, session_secret="d", port=8801, doctor=lambda: {"inputs": {"pbix": {"available": False,
                                                                                                     "reason": "x"}}})
        c = TestClient(app, base_url="http://127.0.0.1:8801")
        mutate = {"X-Bidoc-Session": "d", "X-Requested-With": "bidoc"}
        self.assertEqual(c.get("/api/library").json()["state"], "not_connected")
        bad = c.post("/api/library", json={"url": self.library.url, "token": "bidocpt_" + "0" * 16 + "_" + "a" * 43},
                     headers=mutate)
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (400, "CREDENTIAL_REJECTED"))
        self.assertEqual(c.post("/api/library", json={"url": "http://library.example.com", "token": "x"},
                                headers=mutate).json()["error"]["code"], "INSECURE_URL")
        ok = c.post("/api/library", json={"url": self.library.url, "token": self.token()["token"]}, headers=mutate)
        self.assertEqual(ok.json()["state"], "connected", ok.text)
        batch = runner.submit([str(self.factory)], Options(output_dir=str(self.home / "out"), profile="shared"))
        self.assertTrue(runner.wait(60))
        item = runner.history.batch(batch)["items"][0]
        r = c.post(f"/api/items/{item['item_id']}/publish", json={}, headers=mutate)
        self.assertEqual((r.status_code, r.json()["status"]), (200, "published"), r.text)
        self.assertEqual(c.post(f"/api/items/{item['item_id']}/publish", json={}, headers=mutate).json()["status"],
                         "duplicate")
        self.assertEqual(c.delete("/api/library", headers=mutate).json()["state"], "not_connected")
        self.assertEqual(c.post(f"/api/items/{item['item_id']}/publish", json={}, headers=mutate).json()["error"]["code"],
                         "NOT_CONNECTED")

    test_connect_publish_retry_new_version_and_revocation = None
    test_connect_refuses_a_bad_token_and_keeps_nothing = None
    test_partial_failure_exits_5 = None


class Transport(unittest.TestCase):
    def test_only_https_or_loopback(self):
        self.assertEqual(normalize_url("https://docs.example.com/"), "https://docs.example.com")
        self.assertEqual(normalize_url("https://docs.example.com/api/v1"), "https://docs.example.com")
        self.assertEqual(normalize_url("http://127.0.0.1:8765"), "http://127.0.0.1:8765")
        for bad in ("http://docs.example.com", "ftp://x", "https://user:pw@docs.example.com", "docs.example.com",
                    "https://docs.example.com/?next=x"):
            with self.assertRaises(PublishError):
                normalize_url(bad)

    def test_redirects_are_never_followed(self):
        class Redirect(http.server.BaseHTTPRequestHandler):
            seen = []

            def do_GET(self):
                Redirect.seen.append(self.headers.get("Authorization"))
                self.send_response(307)
                self.send_header("Location", "http://127.0.0.1:1/steal")
                self.end_headers()

            def log_message(self, *a):
                pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
        client = LibraryClient(f"http://127.0.0.1:{server.server_address[1]}", "bidocpt_x")
        with self.assertRaises(PublishError) as ctx:
            client.capabilities()
        self.assertEqual(ctx.exception.code, "REDIRECT_REFUSED")
        self.assertEqual(len(Redirect.seen), 1)
        self.assertNotIn("bidocpt_x", repr(client))


if __name__ == "__main__":
    unittest.main()
