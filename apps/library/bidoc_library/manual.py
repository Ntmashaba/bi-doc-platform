"""Manual relationships (handoff section 16): publisher assertions with a reason.

Labelled "Manual — user asserted"; never turned into detected evidence. Every change
needs the current ETag, writes an audit entry, bumps the record version and advances
the catalogue sequence so a new relationship generation pins it. Deletion is a
tombstone (status 'deleted'), kept for audit and historical generations.
"""
from __future__ import annotations

import uuid

from .errors import LibraryError, conflict, not_found
from .repository import StaleSequence

KINDS = ("related_to", "produces", "consumes", "deletes")


MAX_ATTEMPTS = 20


class ManualLinks:
    def __init__(self, store, derived):
        self.store, self.derived = store, derived     # store: any repository (repository.py)

    def _check_object(self, document_id, revision_id, object_id, role):
        if object_id is None:
            return
        ids = {o["object_id"] for o in self.derived.manifest(document_id, revision_id)["objects"]}
        if object_id not in ids:
            raise LibraryError("OBJECT_NOT_FOUND", f"{role} object does not exist in that revision", 422,
                               {"object_id": object_id})

    def _validate(self, body, expected_sequence, seq):
        kind, reason = body.get("kind"), (body.get("reason") or "").strip()
        if kind not in KINDS:
            raise LibraryError("INVALID_REQUEST", f"kind must be one of {', '.join(KINDS)}")
        if not 1 <= len(reason) <= 2000:
            raise LibraryError("INVALID_REQUEST", "a reason of 1-2000 characters is required")
        if kind != "related_to" and (body.get("source_object_id") is None or body.get("target_object_id") is None):
            raise LibraryError("INVALID_REQUEST", "only related_to links may omit the source or target object")
        if expected_sequence is not None and expected_sequence != seq:
            raise conflict("SELECTION_STALE", "the library changed since the selection was made; reload and retry",
                           current_catalogue_sequence=seq)
        docs = {d["document_id"]: d for d in self.store.catalogue_documents()}
        for role in ("source", "target"):
            doc = docs.get(str(body[f"{role}_document_id"]))
            if doc is None or doc["archived"]:
                raise LibraryError("OBJECT_NOT_FOUND", f"{role} document is not available", 422)
            if doc["current_revision_id"] != str(body[f"{role}_revision_id"]):
                raise conflict("SELECTION_STALE", f"the {role} document has a newer revision; reload and retry")
            self._check_object(doc["document_id"], doc["current_revision_id"], body.get(f"{role}_object_id"), role)
        if body["source_document_id"] == body["target_document_id"] and \
                body.get("source_object_id") == body.get("target_object_id"):
            raise LibraryError("INVALID_REQUEST", "a link needs two different ends")
        return kind, reason

    def _optimistic(self, attempt):
        """Run `attempt(seq)` until it commits against an unchanged catalogue."""
        for _ in range(MAX_ATTEMPTS):
            try:
                return attempt(self.store.read_sequence())
            except StaleSequence:
                continue
        raise LibraryError("COMMIT_CONTENDED", "the catalogue was busy; retry", 503)

    def create(self, body: dict, subject: str) -> dict:
        rid = str(uuid.uuid4())

        def attempt(seq):
            kind, reason = self._validate(body, body.get("expected_catalogue_sequence"), seq)
            now = self.store.now()
            record = {"relationship_id": rid, "source_document_id": str(body["source_document_id"]),
                      "source_revision_id": str(body["source_revision_id"]),
                      "source_object_id": body.get("source_object_id"),
                      "target_document_id": str(body["target_document_id"]),
                      "target_revision_id": str(body["target_revision_id"]),
                      "target_object_id": body.get("target_object_id"), "kind": kind, "reason": reason,
                      "creator_subject": subject, "created_at": now, "updated_at": now, "etag": uuid.uuid4().hex,
                      "status": "active", "version": 1}
            self.store.manual_write(seq, record, None, "create", subject, None)
            return record
        return self._optimistic(attempt)

    def get(self, rid) -> dict:
        record = self.store.manual_get(rid)
        if record is None:
            raise not_found("manual relationship")
        return record

    def update(self, rid, expected_etag, changes: dict, subject: str) -> dict:
        allowed = {"kind", "reason", "target_document_id", "target_revision_id", "target_object_id",
                   "expected_catalogue_sequence"}
        if set(changes) - allowed:
            raise LibraryError("INVALID_REQUEST", f"only {', '.join(sorted(allowed))} can change")
        if not expected_etag:
            raise LibraryError("PRECONDITION_REQUIRED", "If-Match is required", 428)

        def attempt(seq):
            before = self.get(rid)
            if before["etag"] != expected_etag:
                raise conflict("REVISION_CONFLICT", "the link changed since it was read", current_etag=before["etag"])
            merged = {**before, **{k: (str(v) if k in ("target_document_id", "target_revision_id") and v else v)
                                   for k, v in changes.items() if k != "expected_catalogue_sequence"}}
            if {"target_document_id", "target_revision_id", "target_object_id"} & set(changes):
                kind, reason = self._validate(merged, changes.get("expected_catalogue_sequence"), seq)
            else:
                kind, reason = merged["kind"], (merged["reason"] or "").strip()
                if kind not in KINDS or not 1 <= len(reason) <= 2000:
                    raise LibraryError("INVALID_REQUEST", "invalid kind or reason")
            after = {**merged, "kind": kind, "reason": reason, "updated_at": self.store.now(),
                     "etag": uuid.uuid4().hex, "version": before["version"] + 1}
            self.store.manual_write(seq, after, expected_etag, "update", subject, before)
            return after
        return self._optimistic(attempt)

    def delete(self, rid, expected_etag, subject: str) -> None:
        if not expected_etag:
            raise LibraryError("PRECONDITION_REQUIRED", "If-Match is required", 428)

        def attempt(seq):
            before = self.get(rid)
            if before["etag"] != expected_etag:
                raise conflict("REVISION_CONFLICT", "the link changed since it was read", current_etag=before["etag"])
            tomb = {**before, "status": "deleted", "updated_at": self.store.now(), "etag": uuid.uuid4().hex,
                    "version": before["version"] + 1}
            self.store.manual_write(seq, tomb, expected_etag, "delete", subject, before)
        self._optimistic(attempt)

    def audit(self, rid) -> list:
        return self.store.manual_audit(rid)
