"""Worker mode against a real library server (R3, B14; A16, A17, A39 end to end)."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "packages" / "engines" / "tests"))
from fixtures import pbi_model  # noqa: E402
from test_batch import FAKE, pid_alive  # noqa: E402
from test_publish import SECRET, Library, run  # noqa: E402

from bidoc_generator import worker as worker_mod  # noqa: E402
from bidoc_generator.worker import Worker, WorkerClient, extract_project_zip  # noqa: E402


def project_zip(model_bim: Path, extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Sales/Sales.pbip", json.dumps({"version": "1.0", "artifacts": [
            {"semanticModel": {"path": "Sales.SemanticModel"}}]}))
        zf.writestr("Sales/Sales.SemanticModel/model.bim", model_bim.read_text(encoding="utf-8"))
        for name, data in (extra or {}).items():
            info = zipfile.ZipInfo("placeholder")
            info.filename = name                          # raw name, even "\\" on Windows
            zf.writestr(info, data)
    return buf.getvalue()


class WorkerTest(unittest.TestCase):
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
        self.model = pbi_model(self.home / "model")
        os.environ["FAKE_PBI_MODEL"] = str(self.model)
        os.environ["FAKE_PBI_MODE"] = "ok"
        enrolled = self.library.call("POST", "/api/v1/workers", {"label": f"test-{uuid.uuid4().hex[:6]}"})
        self.token = enrolled["token"]
        self.addCleanup(self.revoke, enrolled["worker_id"])

    def revoke(self, worker_id):
        req = urllib.request.Request(f"{self.library.url}/api/v1/workers/{worker_id}", method="DELETE",
                                     headers={"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc"})
        urllib.request.urlopen(req).close()

    def worker(self, **kw):
        kw.setdefault("tool_command", FAKE)
        w = Worker(WorkerClient(self.library.url, self.token), workspace_root=self.home / "ws", log=lambda m: None, **kw)
        w.heartbeat()
        return w

    def submit(self, data: bytes, input_type: str, filename: str):
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="input_type"\r\n\r\n{input_type}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{self.library.url}/api/v1/jobs", data=body, method="POST", headers={
            "X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc",
            "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def job(self, job_id):
        with urllib.request.urlopen(f"{self.library.url}/api/v1/jobs/{job_id}") as r:
            return json.loads(r.read())

    def test_pbix_job_is_extracted_generated_and_published(self):
        w = self.worker()
        job = self.submit(b"PK fake pbix", "pbix", "Sales Report.pbix")
        self.assertEqual(w.run(once=True), 1)
        j = self.job(job["job_id"])
        self.assertEqual(j["state"], "succeeded", j)
        with urllib.request.urlopen(f"{self.library.url}/api/v1/documents/{j['output_document_id']}/revisions") as r:
            revs = json.loads(r.read())["items"]
        self.assertEqual([r["revision_id"] for r in revs], [j["output_revision_id"]])
        self.assertFalse((self.home / "ws" / job["job_id"]).exists())             # workspace removed

    def test_cli_connect_and_run_a_project_zip(self):
        code, out, err = run(["worker", "connect", self.library.url, "--token-stdin"], stdin=self.token + "\n")
        self.assertEqual(code, 0, err)
        self.assertNotIn(self.token, (self.home / "config.json").read_text())
        job = self.submit(project_zip(self.model), "pbip_zip", "Sales.zip")
        code, out, err = run(["worker", "run", "--once"])
        self.assertEqual(code, 0, err)
        self.assertIn("Processed 1 job(s).", out)
        self.assertEqual(self.job(job["job_id"])["state"], "succeeded")
        self.assertEqual(run(["worker", "disconnect"])[0], 0)
        self.assertEqual(run(["worker", "run", "--once"])[0], 3)                   # not connected

    def test_bad_input_fails_without_retry(self):
        w = self.worker()
        job = self.submit(b"not a zip", "pbip_zip", "broken.zip")
        w.run(once=True)
        j = self.job(job["job_id"])
        self.assertEqual((j["state"], j["error"]["code"], j["error"]["retryable"]), ("failed", "INVALID_INPUT", False))

    def test_extraction_failure_is_reported(self):
        os.environ["FAKE_PBI_MODE"] = "fail"
        w = self.worker()
        job = self.submit(b"PK fake pbix", "pbix", "Broken.pbix")
        w.run(once=True)
        j = self.job(job["job_id"])
        self.assertEqual((j["state"], j["error"]["code"]), ("failed", "EXTRACTION_FAILED"))

    def test_a17_cancel_kills_the_tree_cleans_up_and_the_next_job_runs(self):
        os.environ["FAKE_PBI_MODE"] = "tree"
        pidfile = self.home / "grandchild.pid"
        os.environ["FAKE_PBI_PIDFILE"] = str(pidfile)
        old = worker_mod.RENEW_SECONDS
        worker_mod.RENEW_SECONDS = 0.2
        self.addCleanup(setattr, worker_mod, "RENEW_SECONDS", old)
        w = self.worker()
        job = self.submit(b"PK fake pbix", "pbix", "Slow.pbix")
        runner = threading.Thread(target=w.run, kwargs={"once": True})
        runner.start()
        for _ in range(200):
            if pidfile.exists() and pidfile.read_text():
                break
            time.sleep(0.05)
        grandchild = int(pidfile.read_text())
        self.assertTrue(pid_alive(grandchild))
        req = urllib.request.Request(f"{self.library.url}/api/v1/jobs/{job['job_id']}/cancel", method="POST",
                                     headers={"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc"})
        urllib.request.urlopen(req).close()
        runner.join(30)
        self.assertFalse(runner.is_alive())
        self.assertEqual(self.job(job["job_id"])["state"], "cancelled")
        for _ in range(50):
            if not pid_alive(grandchild):
                break
            time.sleep(0.05)
        self.assertFalse(pid_alive(grandchild), "the grandchild survived cancellation")
        self.assertFalse((self.home / "ws" / job["job_id"]).exists())
        os.environ["FAKE_PBI_MODE"] = "ok"
        nxt = self.submit(b"PK fake pbix", "pbix", "Next.pbix")
        self.assertEqual(self.worker().run(once=True), 1)
        self.assertEqual(self.job(nxt["job_id"])["state"], "succeeded")


class ProjectZip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.model = pbi_model(self.tmp / "m")

    def extract(self, data):
        (self.tmp / "a.zip").write_bytes(data)
        return extract_project_zip(self.tmp / "a.zip", self.tmp / "root")

    def test_keeps_sibling_folders(self):
        pbip = self.extract(project_zip(self.model))
        self.assertEqual(pbip.name, "Sales.pbip")
        self.assertTrue((pbip.parent / "Sales.SemanticModel" / "model.bim").is_file())

    def test_refuses_unsafe_archives(self):
        for extra in ({"../evil.txt": "x"}, {"/abs.txt": "x"}, {"a\\b.txt": "x"}, {"C:/x.txt": "x"},
                      {"Other/Other.pbip": "{}"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.extract(project_zip(self.model, extra))
        self.assertFalse((self.tmp / "evil.txt").exists())


if __name__ == "__main__":
    unittest.main()
