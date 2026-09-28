"""Desktop app API: loopback-only, session secret for changes, sandboxed document preview."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

from bidoc_generator.batch import Runner  # noqa: E402
from bidoc_generator.desktop.app import create_app  # noqa: E402
from bidoc_generator.extract import python_tool  # noqa: E402
from bidoc_generator.history import History  # noqa: E402

PORT, SECRET = 8799, "desktop-secret"
MUTATE = {"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc"}
DOCTOR = {"platform": "Test", "checks": [], "inputs": {"pbix": {"available": False, "reason": "pbi-tools not found"},
                                                         "adf_git": {"available": True, "reason": None}}}


class Desktop(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runner = Runner(History(self.tmp / "home"), tool_command=python_tool(str(HERE / "fake_pbi_tools.py")))
        self.addCleanup(self.runner.shutdown)
        app = create_app(self.runner, session_secret=SECRET, port=PORT, doctor=lambda: DOCTOR)
        self.c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")

    def wait_batch(self, batch_id):
        for _ in range(300):
            b = self.c.get(f"/api/batches/{batch_id}").json()
            if all(i["state"] not in ("queued", "validating", "extracting", "analysing", "rendering") for i in b["items"]):
                return b
            time.sleep(0.05)
        self.fail("batch did not finish")

    def test_shell_has_strict_csp_and_session(self):
        r = self.c.get("/")
        self.assertIn("script-src 'self'", r.headers["content-security-policy"])
        self.assertIn(SECRET, r.text)
        self.assertEqual(self.c.get("/static/desktop.js").status_code, 200)
        self.assertEqual(self.c.get("/static/index.html").status_code, 404)
        self.assertEqual(self.c.get("/static/..%2fapp.py").status_code, 404)

    def test_host_origin_and_secret(self):
        self.assertEqual(self.c.get("/api/state", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.c.get("/api/state", headers={"Origin": "http://evil.example"}).status_code, 403)
        body = {"inputs": ["x"], "output_dir": str(self.tmp)}
        self.assertEqual(self.c.post("/api/batches", json=body).status_code, 401)
        self.assertEqual(self.c.post("/api/batches", json=body, headers={"X-Bidoc-Session": SECRET}).status_code, 401)
        self.assertEqual(self.c.post("/api/batches", json=body, headers={**MUTATE, "X-Bidoc-Session": "no"}).status_code, 401)

    def test_review_run_view_and_retry(self):
        factory = str(adf_factory(self.tmp / "factory"))
        pbix = self.tmp / "r.pbix"
        pbix.write_bytes(b"PK")
        review = self.c.post("/api/review", json={"inputs": [factory, str(pbix), str(self.tmp / "none")]}, headers=MUTATE)
        items = review.json()["items"]
        self.assertEqual(items[0]["kind"], "adf_git")
        self.assertIn("unavailable", items[1]["warnings"][0])
        self.assertEqual(items[2]["errors"][0]["code"], "INVALID_INPUT")
        self.assertEqual(self.c.post("/api/batches", json={"inputs": [factory], "output_dir": "relative"},
                                     headers=MUTATE).status_code, 400)
        b = self.c.post("/api/batches", json={"inputs": [factory, str(self.tmp / "none")],
                                              "output_dir": str(self.tmp / "out"), "profile": "shared"}, headers=MUTATE)
        self.assertEqual(b.status_code, 201, b.text)
        b = self.wait_batch(b.json()["batch_id"])
        ok, bad = b["items"]
        self.assertEqual((ok["state"], bad["state"]), ("completed", "failed"))
        view = self.c.get(f"/api/items/{ok['item_id']}/view")
        csp = view.headers["content-security-policy"]
        self.assertTrue(csp.startswith("sandbox allow-scripts"))
        self.assertIn("connect-src 'none'", csp)
        self.assertNotIn("allow-same-origin", csp)
        self.assertEqual(self.c.get(f"/api/items/{bad['item_id']}/view").status_code, 404)
        self.assertEqual(self.c.post(f"/api/items/{ok['item_id']}/retry", headers=MUTATE).status_code, 409)
        self.assertEqual(self.c.post(f"/api/items/{ok['item_id']}/cancel", headers=MUTATE).status_code, 409)
        adf_factory(self.tmp / "none")
        self.assertEqual(self.c.post(f"/api/items/{bad['item_id']}/retry", headers=MUTATE).status_code, 200)
        b = self.wait_batch(b["batch_id"])
        self.assertEqual([i["state"] for i in b["items"]], ["completed", "completed"])
        self.assertEqual(self.c.get("/api/batches").json()[0]["batch_id"], b["batch_id"])
        self.assertEqual(self.c.get("/api/items/nope/view").status_code, 404)


if __name__ == "__main__":
    unittest.main()
