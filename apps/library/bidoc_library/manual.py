"""Manual relationships (handoff section 16): publisher assertions with a reason.

Labelled "Manual — user asserted"; never turned into detected evidence. Every change
needs the current ETag, writes an audit entry, bumps the record version and advances
the catalogue sequence so a new relationship generation pins it. Deletion is a
tombstone (status 'deleted'), kept for audit and historical generations.
"""
from __future__ import annotations

import json
import uuid

from .errors import LibraryError, conflict, not_found

KINDS = ("related_to", "produces", "consumes", "deletes")


class ManualLinks:
    def __init__(self, store, derived):
        self.store, self.derived = store, derived

    def _record(self, row) -> dict:
        return {k: row[k] for k in row.keys()}

    def _check_object(self, document_id, revision_id, object_id, role):
        if object_id is None:
            return
        ids = {o["object_id"] for o in self.derived.manifest(document_id, revision_id)["objects"]}
        if object_id not in ids:
            raise LibraryError("OBJECT_NOT_FOUND", f"{role} object does not exist in that revision", 422,
                               {"object_id": object_id})

    def _validate(self, conn, body, expected_sequence):
        kind, reason = body.get("kind"), (body.get("reason") or "").strip()
        if kind not in KINDS:
            raise LibraryError("INVALID_REQUEST", f"kind must be one of {', '.join(KINDS)}")
        if not 1 <= len(reason) <= 2000:
            raise LibraryError("INVALID_REQUEST", "a reason of 1-2000 characters is required")
        if kind != "related_to" and (body.get("source_object_id") is None or body.get("target_object_id") is None):
            raise LibraryError("INVALID_REQUEST", "only related_to links may omit the source or target object")
        seq = conn.execute("SELECT sequence FROM catalogue_state").fetchone()[0]
        if expected_sequence is not None and expected_sequence != seq:
            raise conflict("SELECTION_STALE", "the library changed since the selection was made; reload and retry",
                           current_catalogue_sequence=seq)
        for role in ("source", "target"):
            doc = conn.execute("SELECT * FROM documents WHERE document_id=?", (body[f"{role}_document_id"],)).fetchone()
            if doc is None or doc["archived"]:
                raise LibraryError("OBJECT_NOT_FOUND", f"{role} document is not available", 422)
            if doc["current_revision_id"] != body[f"{role}_revision_id"]:
                raise conflict("SELECTION_STALE", f"the {role} document has a newer revision; reload and retry")
            self._check_object(body[f"{role}_document_id"], body[f"{role}_revision_id"],
                               body.get(f"{role}_object_id"), role)
        if body["source_document_id"] == body["target_document_id"] and \
                body.get("source_object_id") == body.get("target_object_id"):
            raise LibraryError("INVALID_REQUEST", "a link needs two different ends")
        return kind, reason

    def _audit(self, conn, rid, action, subject, before, after):
        conn.execute("INSERT INTO relationship_audit (relationship_id, action, subject, occurred_at, before, after) "
                     "VALUES (?,?,?,?,?,?)", (rid, action, subject, self.store.now(),
                                              json.dumps(before) if before else None, json.dumps(after) if after else None))
        conn.execute("UPDATE catalogue_state SET sequence = sequence + 1 WHERE id=1")

    def create(self, body: dict, subject: str) -> dict:
        with self.store._db() as conn, self.store._tx(conn):
            kind, reason = self._validate(conn, body, body.get("expected_catalogue_sequence"))
            rid, now = str(uuid.uuid4()), self.store.now()
            conn.execute("INSERT INTO manual_relationships (relationship_id, source_document_id, source_revision_id, "
                         "source_object_id, target_document_id, target_revision_id, target_object_id, kind, reason, "
                         "creator_subject, created_at, updated_at, etag, status, version) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', 1)",
                         (rid, body["source_document_id"], body["source_revision_id"], body.get("source_object_id"),
                          body["target_document_id"], body["target_revision_id"], body.get("target_object_id"),
                          kind, reason, subject, now, now, uuid.uuid4().hex))
            record = self._record(conn.execute("SELECT * FROM manual_relationships WHERE relationship_id=?",
                                               (rid,)).fetchone())
            self._audit(conn, rid, "create", subject, None, record)
        return record

    def get(self, rid) -> dict:
        with self.store._db() as conn:
            row = conn.execute("SELECT * FROM manual_relationships WHERE relationship_id=? AND status != 'deleted'",
                               (rid,)).fetchone()
        if row is None:
            raise not_found("manual relationship")
        return self._record(row)

    def update(self, rid, expected_etag, changes: dict, subject: str) -> dict:
        allowed = {"kind", "reason", "target_document_id", "target_revision_id", "target_object_id",
                   "expected_catalogue_sequence"}
        if set(changes) - allowed:
            raise LibraryError("INVALID_REQUEST", f"only {', '.join(sorted(allowed))} can change")
        if not expected_etag:
            raise LibraryError("PRECONDITION_REQUIRED", "If-Match is required", 428)
        with self.store._db() as conn, self.store._tx(conn):
            row = conn.execute("SELECT * FROM manual_relationships WHERE relationship_id=? AND status != 'deleted'",
                               (rid,)).fetchone()
            if row is None:
                raise not_found("manual relationship")
            if row["etag"] != expected_etag:
                raise conflict("REVISION_CONFLICT", "the link changed since it was read", current_etag=row["etag"])
            before = self._record(row)
            merged = {**before, **{k: v for k, v in changes.items() if k != "expected_catalogue_sequence"}}
            if "target_document_id" in changes or "target_revision_id" in changes or "target_object_id" in changes:
                kind, reason = self._validate(conn, merged, changes.get("expected_catalogue_sequence"))
            else:
                kind, reason = merged["kind"], (merged["reason"] or "").strip()
                if kind not in KINDS or not 1 <= len(reason) <= 2000:
                    raise LibraryError("INVALID_REQUEST", "invalid kind or reason")
            conn.execute("UPDATE manual_relationships SET kind=?, reason=?, target_document_id=?, target_revision_id=?, "
                         "target_object_id=?, updated_at=?, etag=?, version=version+1 WHERE relationship_id=?",
                         (kind, reason, merged["target_document_id"], merged["target_revision_id"],
                          merged["target_object_id"], self.store.now(), uuid.uuid4().hex, rid))
            after = self._record(conn.execute("SELECT * FROM manual_relationships WHERE relationship_id=?",
                                              (rid,)).fetchone())
            self._audit(conn, rid, "update", subject, before, after)
        return after

    def delete(self, rid, expected_etag, subject: str) -> None:
        if not expected_etag:
            raise LibraryError("PRECONDITION_REQUIRED", "If-Match is required", 428)
        with self.store._db() as conn, self.store._tx(conn):
            row = conn.execute("SELECT * FROM manual_relationships WHERE relationship_id=? AND status != 'deleted'",
                               (rid,)).fetchone()
            if row is None:
                raise not_found("manual relationship")
            if row["etag"] != expected_etag:
                raise conflict("REVISION_CONFLICT", "the link changed since it was read", current_etag=row["etag"])
            conn.execute("UPDATE manual_relationships SET status='deleted', updated_at=?, etag=?, version=version+1 "
                         "WHERE relationship_id=?", (self.store.now(), uuid.uuid4().hex, rid))
            self._audit(conn, rid, "delete", subject, self._record(row), None)

    def audit(self, rid) -> list:
        with self.store._db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM relationship_audit WHERE relationship_id=? "
                                                  "ORDER BY audit_id", (rid,))]
