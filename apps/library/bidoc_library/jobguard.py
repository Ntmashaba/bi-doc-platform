"""The publication-time lease check shared by both stores (handoff 10, 17.6; B14).

A job publishes only through server finalization. The import carries the job ID, attempt
and lease-token hash it was started under; the store re-reads the job row inside the
commit (one SQLite transaction, or the Azure commit partition with the job row's ETag) and
publishes only if that lease is still current. The same commit marks the job succeeded.
"""
from __future__ import annotations

ACTIVE = ("leased", "running", "publishing")


def job_fields(job) -> tuple:
    return (job["job_id"], job["attempt"], job["lease_hash"]) if job else (None, None, None)


def lease_current(body, attempt, lease_hash, now: str) -> bool:
    return bool(body) and body.get("state") in ACTIVE and body.get("attempt") == attempt \
        and body.get("lease_token_hash") == lease_hash and not body.get("cancellation_requested") \
        and (body.get("lease_expires_at") or "") > now


def finalized(body, document_id, revision_id, sequence, now) -> dict:
    return {**{k: v for k, v in body.items() if k != "_v"}, "state": "succeeded", "stage": None,
            "output_document_id": document_id, "output_revision_id": revision_id, "catalogue_sequence": sequence,
            "completed_at": now, "lease_token_hash": None, "lease_expires_at": None}
