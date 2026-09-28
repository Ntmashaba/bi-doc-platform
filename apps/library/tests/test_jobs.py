"""Processing workers and jobs (handoff 10, 17.6; A16, A17 server side, A39)."""
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_derived import MUTATE, SECRET, pbi_model  # noqa: E402

from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_library.api import create_app  # noqa: E402
from bidoc_library.config import Settings  # noqa: E402
from bidoc_library.store import LocalStore  # noqa: E402


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> str:
        return self.t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class JobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.clock = Clock()
        store = self.make_store()
        store.now = self.clock
        self.store = store
        self.app = create_app(Settings(local_data_dir=self.tmp / "data"), store=store, session_secret=SECRET)
        self.c = TestClient(self.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 5000))
        self.n = 0

    def make_store(self):
        return LocalStore(self.tmp / "data")

    # ---- helpers ----------------------------------------------------------------------

    def enroll(self, label="build-01"):
        r = self.c.post("/api/v1/workers", json={"label": label}, headers=MUTATE)
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": f"Bearer {r.json()['token']}", "worker_id": r.json()["worker_id"]}

    def w(self, worker, method, path, **kw):
        headers = {k: v for k, v in worker.items() if k == "Authorization"}
        return self.c.request(method, "/api/v1/worker" + path, headers={**headers, **kw.pop("headers", {})}, **kw)

    def ready(self, worker):
        r = self.w(worker, "POST", "/heartbeat", json={"engine_version": "0.2.0", "extractor_version": "pbi-tools 1.2",
                                                         "input_types": ["pbix", "pbip_zip"], "readiness": "ready"})
        self.assertEqual(r.status_code, 204, r.text)

    def submit(self, data=b"PK\x03\x04 fake pbip zip", input_type="pbip_zip", **form):
        return self.c.post("/api/v1/jobs", files={"file": ("sales.zip", data, "application/zip")},
                           data={"input_type": input_type, **form}, headers=MUTATE)

    def claim(self, worker):
        r = self.w(worker, "POST", "/claim")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def artifact(self, title="Sales model"):
        self.n += 1
        r = generate(GenerateRequest(engine="power_bi", source_path=str(pbi_model(self.tmp / "pbi")), source_kind="bim",
                                     output_dir=str(self.tmp / f"out{self.n}"), environment="Production",
                                     profile="shared", title=title))
        self.assertEqual(r.status, "completed", r.errors)
        return Path(r.artifact_path).read_bytes()

    def stage(self, worker, claim, data=None):
        r = self.w(worker, "POST", f"/jobs/{claim['job']['job_id']}/results",
                   files={"file": ("document.html", data or self.artifact(), "text/html")},
                   data={"lease_token": claim["lease_token"], "attempt_id": claim["job"]["attempt_id"]})
        return r

    def complete(self, worker, claim, staged_id):
        return self.w(worker, "POST", f"/jobs/{claim['job']['job_id']}/complete",
                      json={"lease_token": claim["lease_token"], "attempt_id": claim["job"]["attempt_id"],
                            "staged_result_id": staged_id})

    def job(self, job_id):
        return self.c.get(f"/api/v1/jobs/{job_id}").json()

    def revisions(self, document_id):
        return self.c.get(f"/api/v1/documents/{document_id}/revisions").json()["items"]


class Availability(JobTest):
    def test_no_worker_means_processing_is_unavailable(self):                       # A16
        caps = self.c.get("/api/v1/capabilities").json()
        self.assertEqual(caps["worker_status"], "none_enrolled")
        self.assertFalse(caps["processing"][0]["available"])
        r = self.submit()
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "WORKER_UNAVAILABLE"))
        worker = self.enroll()
        self.assertEqual(self.c.get("/api/v1/capabilities").json()["worker_status"], "unavailable")
        self.ready(worker)
        caps = self.c.get("/api/v1/capabilities").json()
        self.assertEqual((caps["worker_status"], caps["processing"][0]["available"]), ("ready", True))
        self.clock.advance(91)                                                  # readiness expires after 90 s
        self.assertFalse(self.c.get("/api/v1/capabilities").json()["processing"][0]["available"])
        self.assertEqual(self.submit().status_code, 409)
        self.assertEqual(self.c.get("/api/v1/documents").status_code, 200)       # browsing unaffected

    def test_worker_tokens_reach_only_the_worker_namespace(self):
        worker = self.enroll()
        bad = {"Authorization": "Bearer bidocwk_0000000000000000_" + "a" * 43}
        self.assertEqual(self.w(bad, "POST", "/claim").status_code, 401)
        self.assertEqual(self.c.post("/api/v1/worker/claim").status_code, 401)
        auth = {"Authorization": worker["Authorization"]}
        self.assertEqual(self.c.post("/api/v1/jobs", headers={**auth, "X-Requested-With": "bidoc"},
                                     files={"file": ("a", b"x")}, data={"input_type": "pbix"}).status_code, 401)
        self.assertEqual(self.c.delete(f"/api/v1/workers/{worker['worker_id']}", headers=MUTATE).status_code, 204)
        self.assertEqual(self.w(worker, "POST", "/claim").status_code, 401)            # revoked
        self.assertNotIn("token", self.c.get("/api/v1/workers").json()["items"][0])


class Lifecycle(JobTest):
    def setUp(self):
        super().setUp()
        self.worker = self.enroll()
        self.ready(self.worker)

    def test_complete_publishes_once_as_the_requester(self):
        source = b"PK\x03\x04 the project" * 1000
        job = self.submit(source).json()
        self.assertEqual(job["state"], "queued")
        claim = self.claim(self.worker)
        self.assertEqual((claim["job"]["job_id"], claim["job"]["attempt"]), (job["job_id"], 1))
        self.assertEqual(self.w(self.worker, "POST", "/claim").status_code, 204)       # nothing else queued
        got = self.w(self.worker, "GET", claim["input_download_url"][len("/api/v1/worker"):],
                     headers={"X-Lease-Token": claim["lease_token"]})
        self.assertEqual(got.content, source)
        self.assertEqual(self.w(self.worker, "GET", claim["input_download_url"][len("/api/v1/worker"):],
                                headers={"X-Lease-Token": "wrong"}).status_code, 409)
        prog = self.w(self.worker, "POST", f"/jobs/{job['job_id']}/progress",
                      json={"lease_token": claim["lease_token"], "stage": "extracting"})
        self.assertEqual(prog.status_code, 204)
        self.assertEqual((self.job(job["job_id"])["state"], self.job(job["job_id"])["stage"]), ("running", "extracting"))
        staged = self.stage(self.worker, claim)
        self.assertEqual(staged.status_code, 201, staged.text)
        self.assertEqual(self.revisions_count(), 0)                              # staging publishes nothing
        done = self.complete(self.worker, claim, staged.json()["staged_result_id"])
        self.assertEqual(done.status_code, 200, done.text)
        out = done.json()
        self.assertEqual(out["state"], "succeeded")
        revs = self.revisions(out["document_id"])
        self.assertEqual([r["revision_id"] for r in revs], [out["revision_id"]])
        self.assertEqual(revs[0]["publisher_subject"], "local-owner")               # the requester, not the worker
        j = self.job(job["job_id"])
        self.assertEqual((j["state"], j["output_revision_id"]), ("succeeded", out["revision_id"]))
        # a retry of complete, even with a different candidate, returns the persisted outcome
        again = self.complete(self.worker, claim, staged.json()["staged_result_id"])
        self.assertEqual(again.json(), out)
        self.assertEqual(len(self.revisions(out["document_id"])), 1)

    def revisions_count(self):
        return sum(len(self.revisions(d["document_id"])) for d in self.c.get("/api/v1/documents").json()["items"])

    def test_killed_worker_lease_recovers_and_publishes_once(self):             # A16, A39
        job = self.submit().json()
        first = self.claim(self.worker)
        staged_a = self.stage(self.worker, first).json()["staged_result_id"]
        self.clock.advance(121)                                                    # worker A stopped renewing
        b = self.enroll("build-02")
        self.ready(b)
        second = self.claim(b)
        self.assertEqual(second["job"]["attempt"], 2)
        # the stale worker resumes: it cannot renew, stage or finalize
        stale = self.complete(self.worker, first, staged_a)
        self.assertEqual((stale.status_code, stale.json()["error"]["code"]), (409, "LEASE_STALE"))
        self.assertEqual(self.stage(self.worker, first).status_code, 409)
        self.assertEqual(self.w(self.worker, "POST", f"/jobs/{job['job_id']}/renew",
                                json={"lease_token": first["lease_token"]}).status_code, 409)
        staged_b = self.stage(b, second, self.artifact("Sales model (attempt 2)")).json()["staged_result_id"]
        out = self.complete(b, second, staged_b).json()
        self.assertEqual(out["state"], "succeeded")
        self.assertEqual(len(self.revisions(out["document_id"])), 1)
        self.assertEqual(self.complete(self.worker, first, staged_a).json(), out)   # persisted outcome only

    def test_stale_publication_cannot_commit_even_after_preparing(self):        # A39 at the commit boundary
        from bidoc_library.jobs import _hash
        job = self.submit().json()
        first = self.claim(self.worker)
        data = self.artifact()
        self.clock.advance(121)
        b = self.enroll("build-02")
        self.ready(b)
        second = self.claim(b)
        with self.assertRaises(Exception) as ctx:                                  # the old lease, straight at the store
            self.store.publish(data, subject="local-owner", idempotency_key=f"job:{job['job_id']}:1",
                               job={"job_id": job["job_id"], "attempt": 1, "lease_hash": _hash(first["lease_token"])})
        self.assertEqual(ctx.exception.code, "LEASE_STALE")
        self.store.reconcile()
        self.assertEqual(self.revisions_count(), 0)
        staged = self.stage(b, second).json()["staged_result_id"]
        self.assertEqual(self.complete(b, second, staged).json()["state"], "succeeded")
        self.assertEqual(self.revisions_count(), 1)

    def test_concurrent_claims_have_one_winner(self):
        import threading
        self.submit()
        workers = [self.enroll(f"w{i}") for i in range(6)]
        for w in workers:
            self.ready(w)
        jobs, results = self.app.state.jobs, []
        records = [self.store.rec_get("worker", w["worker_id"]) for w in workers]
        threads = [threading.Thread(target=lambda r=r: results.append(jobs.claim(r))) for r in records]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r), 1)

    def test_renewal_keeps_the_lease(self):
        job = self.submit().json()
        claim = self.claim(self.worker)
        for _ in range(5):                                                          # 5 x 60 s > one lease
            self.clock.advance(60)
            r = self.w(self.worker, "POST", f"/jobs/{job['job_id']}/renew", json={"lease_token": claim["lease_token"]})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["cancellation_requested"])
            self.ready(self.worker)
        staged = self.stage(self.worker, claim).json()["staged_result_id"]
        self.assertEqual(self.complete(self.worker, claim, staged).json()["state"], "succeeded")

    def test_attempts_run_out_then_manual_retry(self):
        job = self.submit().json()
        for attempt in (1, 2, 3):
            self.ready(self.worker)
            self.assertEqual(self.claim(self.worker)["job"]["attempt"], attempt)
            self.clock.advance(121)
        j = self.job(job["job_id"])
        self.assertEqual((j["state"], j["error"]["code"]), ("failed", "LEASE_EXPIRED"))
        r = self.c.post(f"/api/v1/jobs/{job['job_id']}/retry", headers=MUTATE)
        self.assertEqual((r.status_code, r.json()["state"], r.json()["max_attempts"]), (202, "queued", 6))
        self.ready(self.worker)
        self.assertEqual(self.claim(self.worker)["job"]["attempt"], 4)

    def test_failures_retry_or_stop(self):
        job = self.submit().json()
        claim = self.claim(self.worker)
        r = self.w(self.worker, "POST", f"/jobs/{job['job_id']}/fail",
                   json={"lease_token": claim["lease_token"], "error_code": "EXTRACTION_TIMEOUT",
                         "message": "pbi-tools did not finish", "retryable": True})
        self.assertEqual(r.json()["state"], "queued")
        claim = self.claim(self.worker)
        r = self.w(self.worker, "POST", f"/jobs/{job['job_id']}/fail",
                   json={"lease_token": claim["lease_token"], "error_code": "invalid input!",
                         "message": "not a PBIX", "retryable": False})
        self.assertEqual((r.json()["state"], r.json()["error"]["code"]), ("failed", "INVALIDINPUT"))
        self.assertEqual(self.w(self.worker, "POST", "/claim").status_code, 204)

    def test_result_must_match_the_job(self):
        claim = self.claim_after_submit()
        r = self.stage(self.worker, claim, b"<html>not an artifact</html>")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (422, "CONTRACT_INVALID"))
        wrong = self.w(self.worker, "POST", f"/jobs/{claim['job']['job_id']}/results",
                       files={"file": ("d.html", self.artifact(), "text/html")},
                       data={"lease_token": claim["lease_token"], "attempt_id": "not-this-attempt"})
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(self.complete(self.worker, claim, "0" * 32).status_code, 404)

    def claim_after_submit(self):
        self.submit()
        return self.claim(self.worker)


class Cancellation(JobTest):
    def setUp(self):
        super().setUp()
        self.worker = self.enroll()
        self.ready(self.worker)

    def test_cancel_queued_and_running(self):                                   # A17 (server side)
        queued = self.submit().json()
        r = self.c.post(f"/api/v1/jobs/{queued['job_id']}/cancel", headers=MUTATE)
        self.assertEqual((r.status_code, r.json()["state"]), (202, "cancelled"))
        self.assertEqual(self.w(self.worker, "POST", "/claim").status_code, 204)
        running = self.submit().json()
        claim = self.claim(self.worker)
        r = self.c.post(f"/api/v1/jobs/{running['job_id']}/cancel", headers=MUTATE)
        self.assertEqual(r.json()["state"], "cancel_requested")
        renew = self.w(self.worker, "POST", f"/jobs/{running['job_id']}/renew", json={"lease_token": claim["lease_token"]})
        self.assertTrue(renew.json()["cancellation_requested"])
        staged = self.stage(self.worker, claim).json()["staged_result_id"]
        self.assertEqual(self.complete(self.worker, claim, staged).status_code, 409)   # nothing is published
        r = self.w(self.worker, "POST", f"/jobs/{running['job_id']}/fail",
                   json={"lease_token": claim["lease_token"], "error_code": "CANCELLED", "retryable": False})
        self.assertEqual(r.json()["state"], "cancelled")
        self.assertEqual(self.c.get("/api/v1/documents").json()["items"], [])

    def test_cancel_after_publication_is_too_late(self):
        job = self.submit().json()
        claim = self.claim(self.worker)
        staged = self.stage(self.worker, claim).json()["staged_result_id"]
        self.assertEqual(self.complete(self.worker, claim, staged).json()["state"], "succeeded")
        r = self.c.post(f"/api/v1/jobs/{job['job_id']}/cancel", headers=MUTATE)
        self.assertEqual((r.status_code, r.json()["error"]["details"]["outcome"]), (409, "too_late"))
        j = self.job(job["job_id"])
        self.assertEqual((j["state"], j["cancel_outcome"]), ("succeeded", "too_late"))

    def test_revoking_a_worker_requeues_its_job(self):
        job = self.submit().json()
        self.claim(self.worker)
        self.c.delete(f"/api/v1/workers/{self.worker['worker_id']}", headers=MUTATE)
        self.assertEqual(self.job(job["job_id"])["state"], "queued")


class Retention(JobTest):
    def test_sources_are_deleted_after_the_retention_period(self):
        worker = self.enroll()
        self.ready(worker)
        job = self.submit().json()
        claim = self.claim(worker)
        staged = self.stage(worker, claim).json()["staged_result_id"]
        self.complete(worker, claim, staged)
        jobs = self.app.state.jobs
        self.assertEqual(jobs.cleanup(), {"sources_removed": 0})
        self.clock.advance(24 * 3600 + 1)
        self.assertEqual(jobs.cleanup(), {"sources_removed": 1})
        self.assertIsNone(self.store.rec_get("job", job["job_id"])["source_key"])
        self.ready(worker)
        failed = self.submit().json()
        c2 = self.claim(worker)
        self.w(worker, "POST", f"/jobs/{failed['job_id']}/fail", json={"lease_token": c2["lease_token"],
                                                                      "error_code": "BAD", "retryable": False})
        self.clock.advance(24 * 3600 + 1)
        jobs.cleanup()
        r = self.c.post(f"/api/v1/jobs/{failed['job_id']}/retry", headers=MUTATE)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (409, "JOB_NOT_RETRYABLE"))


from backends import AZURE, AzuriteStore, AzureMemoryStore  # noqa: E402

for _prefix, _mixin in [("AzureMemory", AzureMemoryStore)] + ([("Azurite", AzuriteStore)] if AZURE else []):
    for _case in (Availability, Lifecycle, Cancellation, Retention):
        globals()[_prefix + _case.__name__] = type(_prefix + _case.__name__, (_mixin, _case), {})

if __name__ == "__main__":
    unittest.main()
