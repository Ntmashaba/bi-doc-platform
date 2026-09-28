"""Processing workers and jobs (handoff 10, 17.6; R3, B14).

Workers
- An administrator enrolls a worker and receives its token once:
  `bidocwk_<worker_id>_<secret>` (256 random bits; only a SHA-256 is stored). The token
  authenticates only the /api/v1/worker/* namespace, is bound to that worker, and is
  revoked by deleting the worker. Failures are one generic 401.
- Heartbeat every 30 s with versions, input types and readiness; a worker is ready for
  90 s after its last ready heartbeat. Processing is offered only while one is ready.

Jobs (the durable row is authoritative; there is no separate queue)
- States: queued -> leased -> running -> publishing -> succeeded; queued -> cancelled;
  leased/running -> cancel_requested -> cancelled; leased/running/publishing -> failed.
- A claim is a compare-and-set on the job row: a 120 s lease with a fresh token (only its
  hash is stored) and an attempt ID. Renewal every 30 s returns the cancellation flag.
- Expired leases requeue while attempt < max_attempts (3), otherwise fail.
- Workers stage an immutable candidate (POST results), then ask the server to complete.
  The server publishes it as the requesting user; the store re-checks the lease inside the
  commit and marks the job succeeded in the same commit, so a stale worker never
  publishes and a job publishes at most once. A retry of complete returns the persisted
  outcome, whatever bytes a later attempt produced.
- Cancellation before the commit prevents publication; after it, the job stays succeeded
  and the cancel request is reported as too late.
- Raw sources are kept until SOURCE_RETENTION after a terminal state (default 24 h), then
  deleted by cleanup(); failed-job diagnostics keep no raw content.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from bidoc_contracts import ContractError, validate_artifact

from .access import Principal, unauthorized
from .errors import LibraryError, conflict, not_found

TOKEN_RE = re.compile(r"^bidocwk_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$")
INPUT_TYPES = {"pbix": "power_bi", "pbip_zip": "power_bi"}
LEASE = timedelta(seconds=120)
READY_FOR = timedelta(seconds=90)
MAX_ATTEMPTS = 3
SOURCE_RETENTION = timedelta(hours=24)
MAX_SOURCE_BYTES = 1024 ** 3
ACTIVE = ("leased", "running", "publishing", "cancel_requested")
TERMINAL = ("succeeded", "failed", "cancelled")
STAGES = ("downloading", "extracting", "analysing", "rendering", "uploading")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def stale(message="the lease is no longer current") -> LibraryError:
    return conflict("LEASE_STALE", message)


class Jobs:
    def __init__(self, store, *, max_source_bytes=MAX_SOURCE_BYTES, retention=SOURCE_RETENTION):
        self.store, self.max_source_bytes, self.retention = store, max_source_bytes, retention

    def now(self) -> datetime:
        return _parse(self.store.now())

    # ---- compare-and-set helper ---------------------------------------------------------

    def _update(self, job_id, change, *, attempts=20):
        """Apply change(job) -> job|None to the current row until the CAS succeeds."""
        for _ in range(attempts):
            job = self.store.rec_get("job", job_id)
            if job is None:
                raise not_found("job")
            new = change(dict(job))
            if new is None:
                return job
            if self.store.rec_put("job", job_id, new, job["_v"]):
                return self.store.rec_get("job", job_id)
        raise LibraryError("JOB_CONTENDED", "the job was busy; retry", 503)

    # ---- workers --------------------------------------------------------------------

    def enroll(self, label: str, input_types, admin: Principal) -> dict:
        label = (label or "").strip()
        if not 1 <= len(label) <= 100:
            raise LibraryError("INVALID_REQUEST", "a label of 1-100 characters is required")
        types = sorted(set(input_types or INPUT_TYPES))
        if not types or set(types) - set(INPUT_TYPES):
            raise LibraryError("INVALID_REQUEST", f"input_types must be from {', '.join(INPUT_TYPES)}")
        worker_id, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
        token = f"bidocwk_{worker_id}_{secret}"
        record = {"worker_id": worker_id, "label": label, "token_hash": _hash(token), "enrolled_by": admin.subject,
                  "enrolled_at": self.store.now(), "allowed_input_types": types, "input_types": [],
                  "engine_version": None, "extractor_version": None, "readiness": "not_ready",
                  "readiness_detail": None, "last_heartbeat_at": None, "revoked_at": None}
        self.store.rec_put("worker", worker_id, record, None)
        return {**self._public_worker(record), "token": token}

    def _public_worker(self, w) -> dict:
        return {k: w.get(k) for k in ("worker_id", "label", "enrolled_by", "enrolled_at", "allowed_input_types",
                                      "input_types", "engine_version", "extractor_version", "readiness",
                                      "readiness_detail", "last_heartbeat_at", "revoked_at")} | \
            {"ready": self._ready(w)}

    def _ready(self, w) -> bool:
        return (not w.get("revoked_at") and w.get("readiness") == "ready" and bool(w.get("last_heartbeat_at"))
                and self.now() - _parse(w["last_heartbeat_at"]) <= READY_FOR)

    def workers(self) -> list[dict]:
        return [self._public_worker(w) for w in self.store.rec_list("worker")]

    def revoke_worker(self, worker_id: str, admin: Principal) -> None:
        w = self.store.rec_get("worker", worker_id)
        if w is None:
            raise not_found("worker")
        if not w.get("revoked_at"):
            w["revoked_at"] = self.store.now()
            self.store.rec_put("worker", worker_id, w, w["_v"])
        for job in self.store.rec_list("job"):                  # its jobs go back to the queue
            if job.get("worker_id") == worker_id and job["state"] in ACTIVE:
                self._expire(job["job_id"], force=True)

    def authenticate(self, authorization: str | None) -> dict:
        denied = unauthorized("a valid worker token is required")
        scheme, _, token = (authorization or "").partition(" ")
        m = TOKEN_RE.match(token.strip()) if scheme.lower() == "bearer" else None
        if not m:
            raise denied
        w = self.store.rec_get("worker", m.group(1))
        if w is None or w.get("revoked_at") or not hmac.compare_digest(w["token_hash"], _hash(token.strip())):
            raise denied
        return w

    def heartbeat(self, worker: dict, *, engine_version, extractor_version, input_types, readiness,
                  readiness_detail=None) -> None:
        types = sorted(set(input_types or []) & set(worker["allowed_input_types"]))
        if readiness not in ("ready", "not_ready"):
            raise LibraryError("INVALID_REQUEST", "readiness must be ready or not_ready")
        for _ in range(10):
            w = self.store.rec_get("worker", worker["worker_id"])
            w.update(engine_version=str(engine_version or "")[:100], extractor_version=str(extractor_version or "")[:100],
                     input_types=types, readiness=readiness, readiness_detail=str(readiness_detail or "")[:500] or None,
                     last_heartbeat_at=self.store.now())
            if self.store.rec_put("worker", w["worker_id"], w, w["_v"]):
                return

    def processing(self) -> tuple[list[dict], str]:
        """(capabilities processing list, worker status) for /capabilities."""
        workers = [w for w in self.store.rec_list("worker") if not w.get("revoked_at")]
        ready = [w for w in workers if self._ready(w)]
        types = {t for w in ready for t in w.get("input_types", [])}
        status = "ready" if ready else "unavailable" if workers else "none_enrolled"
        reason = None if ready else ("No processing worker is ready; use the generator on your computer."
                                     if workers else "No processing worker is enrolled; use the generator.")
        items = [{"engine": "power_bi", "input_types": sorted(INPUT_TYPES), "available": bool(types),
                  "reason": None if types else reason},
                 {"engine": "adf", "input_types": ["adf_git", "adf_arm", "adf_resources"], "available": False,
                  "reason": "Data Factory sources are documented with the generator; they need no extraction."}]
        return items, status

    # ---- submission (browser) ---------------------------------------------------------

    def submit(self, stream, *, filename: str, input_type: str, document_id: str | None, principal: Principal) -> dict:
        if input_type not in INPUT_TYPES:
            raise LibraryError("INVALID_REQUEST", f"input_type must be one of {', '.join(INPUT_TYPES)}")
        if not any(self._ready(w) and input_type in w.get("input_types", []) for w in self.store.rec_list("worker")):
            raise conflict("WORKER_UNAVAILABLE", "no processing worker is ready for this input; use the generator")
        if document_id:
            self.store.get_document(document_id)              # must exist (404 otherwise)
        job_id = str(uuid.uuid4())
        key = f"jobs/{job_id}/source"
        size = self.store.blob_put(key, stream)
        if size == 0 or size > self.max_source_bytes:
            self.store.blob_delete(key)
            raise LibraryError("PAYLOAD_TOO_LARGE" if size else "INVALID_REQUEST",
                               "the source is empty" if not size else "the source exceeds the upload limit",
                               413 if size else 400)
        job = {"job_id": job_id, "input_type": input_type, "filename": re.sub(r"[^\w .()-]", "_", filename or "")[:200],
               "source_key": key, "source_bytes": size, "document_id": document_id, "requested_by": principal.subject,
               "state": "queued", "stage": None, "attempt": 0, "max_attempts": MAX_ATTEMPTS, "attempt_id": None,
               "worker_id": None, "lease_token_hash": None, "lease_expires_at": None,
               "created_at": self.store.now(), "started_at": None, "completed_at": None, "error": None,
               "output_document_id": None, "output_revision_id": None, "catalogue_sequence": None,
               "cancellation_requested": False, "cancel_outcome": None, "history": []}
        self.store.rec_put("job", job_id, job, None)
        return self.public(job)

    @staticmethod
    def public(job) -> dict:
        return {k: job.get(k) for k in ("job_id", "input_type", "filename", "source_bytes", "document_id",
                                        "requested_by", "state", "stage", "attempt", "max_attempts", "worker_id",
                                        "created_at", "started_at", "completed_at", "error", "output_document_id",
                                        "output_revision_id", "cancellation_requested", "cancel_outcome")}

    def get(self, job_id: str, principal: Principal) -> dict:
        self.expire_leases()
        job = self.store.rec_get("job", job_id)
        if job is None or (job["requested_by"] != principal.subject and not principal.can("admin")):
            raise not_found("job")
        return self.public(job)

    def list(self, principal: Principal) -> list[dict]:
        self.expire_leases()
        jobs = [j for j in self.store.rec_list("job") if principal.can("admin") or j["requested_by"] == principal.subject]
        return [self.public(j) for j in sorted(jobs, key=lambda j: j["created_at"], reverse=True)]

    def cancel(self, job_id: str, principal: Principal) -> dict:
        self.get(job_id, principal)

        def change(job):
            if job["state"] == "succeeded":
                job["cancel_outcome"] = "too_late"            # the revision was already committed
                return job
            if job["state"] in TERMINAL:
                return None
            if job["state"] == "queued":
                job.update(state="cancelled", completed_at=self.store.now(), cancel_outcome="cancelled")
            else:
                job.update(cancellation_requested=True,
                           state="cancel_requested" if job["state"] in ("leased", "running") else job["state"])
            return job
        job = self._update(job_id, change)
        if job["state"] in ("failed", "cancelled") and job.get("cancel_outcome") != "cancelled":
            raise conflict("JOB_TERMINAL", f"the job already {job['state']}")
        if job["state"] == "succeeded":
            raise conflict("JOB_TERMINAL", "the job had already published; cancellation came too late",
                           outcome="too_late", document_id=job["output_document_id"],
                           revision_id=job["output_revision_id"])
        return self.public(job)

    def retry(self, job_id: str, principal: Principal) -> dict:
        self.get(job_id, principal)

        def change(job):
            if job["state"] not in ("failed", "cancelled") or not job.get("source_key"):
                return None
            job.update(state="queued", max_attempts=job["attempt"] + MAX_ATTEMPTS, error=None, completed_at=None,
                       cancellation_requested=False, cancel_outcome=None, worker_id=None, stage=None)
            return job
        job = self._update(job_id, change)
        if job["state"] != "queued":
            raise conflict("JOB_NOT_RETRYABLE", "only a failed or cancelled job whose source is still kept can retry")
        return self.public(job)

    # ---- leases -------------------------------------------------------------------------

    def _expire(self, job_id, *, force=False):
        def change(job):
            if job["state"] not in ACTIVE:
                return None
            if not force and _parse(job["lease_expires_at"]) > self.now():
                return None
            common = dict(lease_token_hash=None, lease_expires_at=None, worker_id=None, stage=None)
            job["history"] = (job.get("history") or [])[-20:] + [
                {"attempt": job["attempt"], "worker_id": job["worker_id"], "outcome": "lease_expired",
                 "at": self.store.now()}]
            if job["cancellation_requested"]:
                job.update(state="cancelled", completed_at=self.store.now(), cancel_outcome="cancelled", **common)
            elif job["attempt"] < job["max_attempts"]:
                job.update(state="queued", **common)
            else:
                job.update(state="failed", completed_at=self.store.now(), **common,
                           error={"code": "LEASE_EXPIRED", "message": "the worker stopped responding on every attempt",
                                  "retryable": True})
            return job
        return self._update(job_id, change)

    def expire_leases(self) -> None:
        now = self.now()
        for job in self.store.rec_list("job"):
            if job["state"] in ACTIVE and job.get("lease_expires_at") and _parse(job["lease_expires_at"]) <= now:
                self._expire(job["job_id"])

    def claim(self, worker: dict):
        """(job, lease_token, lease_expires_at) or None."""
        self.expire_leases()
        w = self.store.rec_get("worker", worker["worker_id"])
        if not self._ready(w):
            return None
        for job in sorted(self.store.rec_list("job"), key=lambda j: j["created_at"]):
            if job["state"] != "queued" or job["input_type"] not in w.get("input_types", []):
                continue
            token = secrets.token_urlsafe(32)
            expires = _iso(self.now() + LEASE)
            new = {**job, "state": "leased", "attempt": job["attempt"] + 1, "attempt_id": str(uuid.uuid4()),
                   "worker_id": w["worker_id"], "lease_token_hash": _hash(token), "lease_expires_at": expires,
                   "started_at": job["started_at"] or self.store.now(), "stage": None}
            if self.store.rec_put("job", job["job_id"], new, job["_v"]):   # compare-and-set: one winner
                return new, token, expires
        return None

    def _leased(self, job_id, worker, lease_token):
        job = self.store.rec_get("job", job_id)
        if job is None:
            raise not_found("job")
        if job["state"] not in ACTIVE or job.get("worker_id") != worker["worker_id"] \
                or not hmac.compare_digest(job.get("lease_token_hash") or "", _hash(lease_token or "")) \
                or _parse(job["lease_expires_at"]) <= self.now():
            raise stale()
        return job

    def renew(self, job_id, worker, lease_token) -> dict:
        self._leased(job_id, worker, lease_token)

        def change(job):
            if job.get("lease_token_hash") != _hash(lease_token) or job["state"] not in ACTIVE:
                raise stale()
            job["lease_expires_at"] = _iso(self.now() + LEASE)
            return job
        job = self._update(job_id, change)
        return {"lease_expires_at": job["lease_expires_at"], "cancellation_requested": job["cancellation_requested"]}

    def progress(self, job_id, worker, lease_token, stage) -> None:
        if stage not in STAGES:
            raise LibraryError("INVALID_REQUEST", f"stage must be one of {', '.join(STAGES)}")
        self._leased(job_id, worker, lease_token)

        def change(job):
            if job.get("lease_token_hash") != _hash(lease_token) or job["state"] not in ACTIVE:
                raise stale()
            job["stage"] = stage
            if job["state"] == "leased":
                job["state"] = "running"
            return job
        self._update(job_id, change)

    def source(self, job_id, worker, lease_token):
        job = self._leased(job_id, worker, lease_token)
        return job, self.store.blob_chunks(job["source_key"])

    def fail(self, job_id, worker, lease_token, *, code, message, retryable) -> dict:
        self._leased(job_id, worker, lease_token)
        code = re.sub(r"[^A-Z0-9_]", "", str(code or "PROCESSING_FAILED").upper())[:60] or "PROCESSING_FAILED"

        def change(job):
            if job.get("lease_token_hash") != _hash(lease_token) or job["state"] not in ACTIVE:
                raise stale()
            common = dict(lease_token_hash=None, lease_expires_at=None, stage=None)
            error = {"code": code, "message": str(message or "")[:1000], "retryable": bool(retryable)}
            job["history"] = (job.get("history") or [])[-20:] + [
                {"attempt": job["attempt"], "worker_id": job["worker_id"], "outcome": code, "at": self.store.now()}]
            if job["cancellation_requested"] or code == "CANCELLED":
                job.update(state="cancelled", completed_at=self.store.now(), cancel_outcome="cancelled", **common)
            elif retryable and job["attempt"] < job["max_attempts"]:
                job.update(state="queued", worker_id=None, error=error, **common)
            else:
                job.update(state="failed", completed_at=self.store.now(), error=error, **common)
            return job
        return self.public(self._update(job_id, change))

    # ---- results and server finalization ---------------------------------------------------

    def stage_result(self, job_id, worker, lease_token, attempt_id, data: bytes) -> dict:
        job = self._leased(job_id, worker, lease_token)
        if attempt_id != job["attempt_id"]:
            raise stale("this attempt is no longer current")
        try:
            manifest = validate_artifact(data, limits=self.store.limits)
        except ContractError as exc:
            raise LibraryError("CONTRACT_INVALID", f"the result is not a valid artifact: {exc}", 422,
                               {"contract_code": exc.code}) from None
        if manifest["document_type"] != INPUT_TYPES[job["input_type"]]:
            raise LibraryError("CONTRACT_INVALID", "the result is not a document of the job's type", 422)
        if job["document_id"] and manifest["document_id"] != job["document_id"]:
            raise LibraryError("CONTRACT_INVALID", "the result is for a different document than the job named", 422)
        staged_id = hashlib.sha256(data).hexdigest()[:32]       # immutable: same bytes, same ID
        key = f"jobs/{job_id}/results/{job['attempt_id']}/{staged_id}.html"
        import io  # noqa: PLC0415
        self.store.blob_put(key, io.BytesIO(data))
        self.store.rec_put("staged", f"{job_id}:{staged_id}", {
            "job_id": job_id, "attempt_id": job["attempt_id"], "staged_result_id": staged_id, "key": key,
            "document_id": manifest["document_id"], "revision_id": manifest["revision_id"],
            "size_bytes": len(data), "staged_at": self.store.now()}, None)
        return {"staged_result_id": staged_id, "document_id": manifest["document_id"],
                "revision_id": manifest["revision_id"]}

    def _outcome(self, job) -> dict:
        return {"state": job["state"], "document_id": job["output_document_id"],
                "revision_id": job["output_revision_id"], "catalogue_sequence": job.get("catalogue_sequence"),
                "cancel_outcome": job.get("cancel_outcome")}

    def complete(self, job_id, worker, lease_token, attempt_id, staged_result_id) -> dict:
        current = self.store.rec_get("job", job_id)
        if current is None:
            raise not_found("job")
        if current["state"] == "succeeded":                    # a retry: the persisted outcome
            return self._outcome(current)
        job = self._leased(job_id, worker, lease_token)
        if attempt_id != job["attempt_id"]:
            raise stale("this attempt is no longer current")
        staged = self.store.rec_get("staged", f"{job_id}:{staged_result_id}")
        if staged is None or staged["attempt_id"] != attempt_id:
            raise LibraryError("NOT_FOUND", "staged result not found for this attempt", 404)
        if job["cancellation_requested"]:
            raise stale("cancellation was requested; the result is not published")

        def to_publishing(j):
            if j.get("lease_token_hash") != _hash(lease_token) or j["state"] not in ("leased", "running", "publishing"):
                raise stale()
            j.update(state="publishing", stage=None)
            return j
        job = self._update(job_id, to_publishing)
        data = b"".join(self.store.blob_chunks(staged["key"]))
        try:
            doc = self.store.get_document(staged["document_id"])
            etag = doc.get("etag")
        except LibraryError as exc:
            if exc.status != 404:
                raise
            etag = None
        try:
            self.store.publish(data, subject=job["requested_by"], idempotency_key=f"job:{job_id}:{job['attempt']}",
                               expected_etag=etag, job={"job_id": job_id, "attempt": job["attempt"],
                                                        "lease_hash": _hash(lease_token)})
        except LibraryError as exc:
            final = self.store.rec_get("job", job_id)
            if final["state"] == "succeeded":
                return self._outcome(final)
            if exc.code == "LEASE_STALE":
                if final.get("cancellation_requested") and final.get("lease_token_hash") == _hash(lease_token):
                    def cancelled(j):
                        if j.get("lease_token_hash") != _hash(lease_token):
                            return None
                        j.update(state="cancelled", completed_at=self.store.now(), cancel_outcome="cancelled",
                                 lease_token_hash=None, lease_expires_at=None, stage=None)
                        return j
                    return self._outcome(self._update(job_id, cancelled))
                raise
            retryable = exc.code in ("REVISION_CONFLICT", "COMMIT_CONTENDED", "IMPORT_INCOMPLETE")
            return self._finish_failed(job_id, lease_token, exc, retryable)
        final = self.store.rec_get("job", job_id)
        if final["state"] != "succeeded":
            # Identical bytes were already published (a byte duplicate): the store recorded
            # the original outcome without a commit, so finalize the job under its lease.
            imp_doc, imp_rev = staged["document_id"], staged["revision_id"]

            def succeed(j):
                if j.get("lease_token_hash") != _hash(lease_token) or j["state"] != "publishing":
                    raise stale()
                j.update(state="succeeded", output_document_id=imp_doc, output_revision_id=imp_rev,
                         completed_at=self.store.now(), lease_token_hash=None, lease_expires_at=None)
                return j
            final = self._update(job_id, succeed)
        return self._outcome(final)

    def _finish_failed(self, job_id, lease_token, exc: LibraryError, retryable: bool) -> dict:
        def change(j):
            if j.get("lease_token_hash") != _hash(lease_token):
                return None
            error = {"code": exc.code, "message": exc.message[:1000], "retryable": retryable}
            if retryable and j["attempt"] < j["max_attempts"]:
                j.update(state="queued", worker_id=None, error=error, lease_token_hash=None, lease_expires_at=None)
            else:
                j.update(state="failed", error=error, completed_at=self.store.now(), lease_token_hash=None,
                         lease_expires_at=None)
            return j
        return self._outcome(self._update(job_id, change))

    # ---- retention ------------------------------------------------------------------------

    def cleanup(self) -> dict:
        """Delete raw sources and staged candidates of jobs terminal for longer than the retention."""
        self.expire_leases()
        cutoff = self.now() - self.retention
        removed = 0
        for job in self.store.rec_list("job"):
            if job["state"] in TERMINAL and job.get("source_key") and job.get("completed_at") \
                    and _parse(job["completed_at"]) <= cutoff:
                self.store.blob_delete(job["source_key"])
                for staged in self.store.rec_list("staged"):
                    if staged["job_id"] == job["job_id"]:
                        self.store.blob_delete(staged["key"])
                self._update(job["job_id"], lambda j: {**j, "source_key": None})
                removed += 1
        return {"sources_removed": removed}
