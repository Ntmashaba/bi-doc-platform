"""Azure backend: Azure Table Storage catalogue plus Blob artifacts (handoff 5 and 17.2).

Same public contract as LocalStore; the shared contract suite runs against both.

Layout (one table):

    partition "documents"          the commit partition
        state                      catalogue_sequence (the durable dirty marker)
        doc:{document_id}          current pointer, catalogue fields, metadata overrides, logical ETag
        stream:{sha256}            one row per (asset, type, environment, scope): stream uniqueness
        event:{seq:012d}           immutable publication / archive / restore / metadata events
        evrev:{revision_id}        "this revision committed at seq": the authoritative commit record
        docev:{doc}:{seq:012d}     per-document history index
        meta:{doc}:{seq:012d}      metadata override audit
    partition "revisions:{doc}"    rev:{revision_id} prepared revision descriptors (not commit)
    partition "imports"            imp:{import_id}, key:{sha256(subject, key)} idempotency,
                                   sha:{submitted sha256} byte-duplicate lookup
    blob artifacts/{doc}/{rev}/document.html   created if absent, never overwritten

A publication commit is one entity-group transaction in the "documents" partition: the
state row (If-Match), the document row (If-Match, or create), the stream row (create, new
documents only), the event, evrev and docev rows. Descriptors and blobs are written first
and are not commit. Import rows are updated afterwards; if that step is lost, the evrev row
still records the outcome and reconciliation repairs the import (section 17.2).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from bidoc_contracts import Limits, validate_artifact

from ..admission import admit, admit_for_preview, preview_body, reprojected
from ..errors import LibraryError, conflict, not_found
from ..store import SimulatedCrash, _cursor_decode, _cursor_encode, _now, _sha256, validate_overrides
from ..repository import StaleSequence
from .backends import Conflict, NotFound

DOCS, IMPORTS, GENERATIONS = "documents", "imports", "generations"
FAR = 10 ** 12                         # descending sequence keys: FAR - seq
MAX_COMMIT_ATTEMPTS = 20
REPLAY_STATUS = {"CONTRACT_INVALID": 422, "UNSUPPORTED_SAFE_PROJECTION": 422, "PAYLOAD_TOO_LARGE": 413,
                 "ARTIFACT_TOO_LARGE": 413, "PRECONDITION_REQUIRED": 428, "INVALID_REQUEST": 400}


def _stream_key(asset_id, document_type, environment_key, scope_key) -> str:
    return "stream:" + hashlib.sha256("\0".join((asset_id, document_type, environment_key, scope_key))
                                      .encode()).hexdigest()


def _revisions(document_id) -> str:
    return f"revisions:{document_id}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AzureStore:
    backend = "azure"

    def __init__(self, tables, blobs, *, limits: Limits = Limits(), faults=(), clock=_now,
                 cleanup_grace_seconds: float = 3600):
        self.tables, self.blobs = tables, blobs
        self.limits, self.faults, self.now = limits, set(faults), clock
        self.cleanup_grace_seconds = cleanup_grace_seconds
        try:
            self.tables.transact(DOCS, [("create", {"RowKey": "state", "sequence": 0})])
        except Conflict:
            pass
        self.reconcile(startup=True)

    def _fault(self, name):
        if name in self.faults:
            raise SimulatedCrash(name)

    @staticmethod
    def artifact_key(document_id, revision_id) -> str:
        return f"artifacts/{document_id}/{revision_id}/document.html"

    # ---- publication ------------------------------------------------------------

    def publish(self, data: bytes, *, subject: str, idempotency_key: str, expected_etag: str | None = None,
                target_document_id: str | None = None, query_code: str = "withheld",
                legacy_metadata=None) -> dict:
        from bidoc_engines import legacy  # noqa: PLC0415
        if not idempotency_key or len(idempotency_key) > 200:
            raise LibraryError("INVALID_REQUEST", "an Idempotency-Key of 1-200 characters is required")
        if query_code not in ("withheld", "included"):
            raise LibraryError("INVALID_REQUEST", "query_code must be withheld or included")
        if len(data) > self.limits.html_bytes:
            raise LibraryError("PAYLOAD_TOO_LARGE", f"the document exceeds the {self.limits.html_bytes}-byte limit", 413)
        try:
            legacy_metadata = legacy.parse_metadata(legacy_metadata)
        except ValueError as exc:
            raise LibraryError("INVALID_REQUEST", str(exc)) from None
        submitted = _sha256(data)
        request = _sha256(json.dumps([submitted, target_document_id, expected_etag, query_code]
                                     + ([legacy_metadata] if legacy_metadata else [])).encode())
        key_row = "key:" + _sha256(f"{subject}\0{idempotency_key}".encode())

        for _ in range(2):
            existing = self.tables.get(IMPORTS, key_row)
            if existing:
                imp = self.tables.get(IMPORTS, "imp:" + existing["import_id"])
                if imp["request_sha256"] != request:
                    raise conflict("IDEMPOTENCY_KEY_REUSED", "this Idempotency-Key was used for a different request")
                if imp["state"] in ("prepared", "validating"):
                    self.reconcile()
                return self._replay(imp["import_id"])
            duplicate = self.tables.get(IMPORTS, "sha:" + submitted)
            import_id = str(uuid.uuid4())
            row = {"RowKey": "imp:" + import_id, "import_id": import_id, "subject": subject,
                   "idempotency_key": idempotency_key, "request_sha256": request,
                   "submitted_artifact_sha256": submitted, "target_document_id": target_document_id or "",
                   "expected_etag": expected_etag or "", "state": "validating", "created_at": self.now()}
            try:
                self.tables.transact(IMPORTS, [("create", row), ("create", {"RowKey": key_row, "import_id": import_id})])
                break
            except Conflict:
                continue                       # the same key raced in: replay its outcome
        else:
            raise LibraryError("INTERNAL_ERROR", "could not record the import", 500)
        if duplicate is not None:
            return self._record_duplicate(import_id, duplicate)
        try:
            return self._publish(import_id, data, subject, expected_etag, target_document_id, query_code, submitted,
                                 legacy_metadata)
        except LibraryError as exc:
            self._fail(import_id, exc)
            raise

    def _update_import(self, import_id, **fields):
        for _ in range(MAX_COMMIT_ATTEMPTS):
            row = self.tables.get(IMPORTS, "imp:" + import_id)
            row.update({k: ("" if v is None else v) for k, v in fields.items()})
            try:
                self.tables.transact(IMPORTS, [("replace", row, row["etag"])])
                return row
            except Conflict:
                continue
        raise LibraryError("INTERNAL_ERROR", "could not update the import", 500)

    def _revision_state(self, document_id, revision_id):
        """committed (evrev exists), or the descriptor's prepared/conflicted, or None."""
        ev = self.tables.get(DOCS, "evrev:" + revision_id)
        if ev:
            return "committed", ev
        rev = self.tables.get(_revisions(document_id), "rev:" + revision_id)
        return (rev["status"], None) if rev else (None, None)

    def _record_duplicate(self, import_id, dup) -> dict:
        state, ev = self._revision_state(dup["document_id"], dup["revision_id"])
        state = {"committed": "committed", "conflicted": "conflicted"}.get(state, "prepared")
        origin = self.tables.get(IMPORTS, "imp:" + dup["import_id"])
        self._update_import(import_id, state=state, document_id=dup["document_id"], revision_id=dup["revision_id"],
                            duplicate_of=dup["import_id"],
                            catalogue_sequence=ev["sequence"] if ev else (origin or {}).get("catalogue_sequence", ""),
                            error_code=(origin or {}).get("error_code", ""),
                            error_message=(origin or {}).get("error_message", ""), completed_at=self.now())
        if state == "prepared":
            self.reconcile()
        return self._replay(import_id)

    def preview(self, data: bytes, *, legacy_metadata=None, query_code: str = "withheld") -> dict:
        kind, manifest = admit_for_preview(data, limits=self.limits, legacy_metadata=legacy_metadata)
        _, stored = reprojected(manifest, query_code)
        existing = self.tables.get(DOCS, "doc:" + manifest["document_id"]) is not None
        dup = self.tables.get(IMPORTS, "sha:" + _sha256(data))
        committed = dup and self._revision_state(dup["document_id"], dup["revision_id"])[0] == "committed"
        return preview_body(kind, stored, existing=existing,
                            duplicate={"document_id": dup["document_id"], "revision_id": dup["revision_id"]}
                            if committed else None)

    def _publish(self, import_id, data, subject, expected_etag, target_document_id, query_code, submitted,
                 legacy_metadata=None):
        manifest = admit(data, limits=self.limits, legacy_metadata=legacy_metadata,
                         target_document_id=target_document_id, get_document=self.get_document)
        document_id, revision_id = manifest["document_id"], manifest["revision_id"]
        if target_document_id and target_document_id != document_id:
            raise LibraryError("INVALID_REQUEST", "target_document_id does not match the artifact's document_id")
        self._check_identity(manifest, expected_etag)
        other = self.tables.get(_revisions(document_id), "rev:" + revision_id)
        if other and other["submitted_artifact_sha256"] != submitted:
            raise conflict("REVISION_BYTES_CONFLICT", "this revision ID was already published with different bytes")
        stored, sm = reprojected(manifest, query_code)
        # Record which revision this import is writing, so an interrupted import can be cleaned up.
        self._update_import(import_id, document_id=document_id, revision_id=revision_id)

        key = self.artifact_key(document_id, revision_id)
        if not self.blobs.create(key, stored) and self.blobs.read(key) != stored:
            raise conflict("REVISION_BYTES_CONFLICT", "a different artifact is already stored for this revision")
        self._fault("after_artifact_write")
        cls, pub = sm["classification"], sm["publication"]
        descriptor = {
            "RowKey": "rev:" + revision_id, "document_id": document_id, "revision_id": revision_id,
            "document_type": sm["document_type"], "schema_version": sm["schema_version"],
            "generated_at": sm["generated_at"], "publisher_subject": subject, "artifact_key": key,
            "submitted_artifact_sha256": submitted, "stored_artifact_sha256": _sha256(stored),
            "content_sha256": sm["content_sha256"], "size_bytes": len(stored), "section_count": len(sm["sections"]),
            "title": sm["title"], "description": sm["description"], "tags": json.dumps(sm["tags"]),
            "business_area": cls["business_area"], "environment": cls["environment"], "owner": cls["owner"],
            "asset_id": pub["asset_id"], "environment_key": pub["environment_key"], "scope_key": pub["scope_key"],
            "status": "prepared", "published_at": ""}
        self.tables.transact(_revisions(document_id), [("upsert", descriptor)])
        try:
            self.tables.transact(IMPORTS, [("create", {"RowKey": "sha:" + submitted, "import_id": import_id,
                                                       "document_id": document_id, "revision_id": revision_id})])
        except Conflict:
            pass
        self._update_import(import_id, state="prepared")
        self._fault("after_prepare")
        self._commit(import_id)
        return self._replay(import_id)

    def _check_identity(self, manifest, expected_etag):
        doc = self.tables.get(DOCS, "doc:" + manifest["document_id"])
        pub = manifest["publication"]
        if doc is None:
            if expected_etag is not None:
                raise conflict("REVISION_CONFLICT", "If-Match was given but the document does not exist yet")
            clash = self.tables.get(DOCS, _stream_key(pub["asset_id"], manifest["document_type"],
                                                      pub["environment_key"], pub["scope_key"]))
            if clash:
                raise conflict("STREAM_IDENTITY_CONFLICT", "another document already publishes this stream",
                               document_id=clash["document_id"])
            return None
        if doc["document_type"] != manifest["document_type"]:
            raise conflict("DOCUMENT_TYPE_CONFLICT", "a document ID cannot change type")
        if (doc["asset_id"], doc["environment_key"], doc["scope_key"]) != \
                (pub["asset_id"], pub["environment_key"], pub["scope_key"]):
            raise conflict("STREAM_IDENTITY_CONFLICT", "asset, environment and scope of a document are immutable")
        if expected_etag is None:
            raise LibraryError("PRECONDITION_REQUIRED", "updating an existing document requires If-Match", 428,
                               {"current_etag": doc["doc_etag"]})
        if expected_etag != doc["doc_etag"]:
            raise conflict("REVISION_CONFLICT", "the document changed since it was read", current_etag=doc["doc_etag"])
        return doc

    @staticmethod
    def _effective(revision_meta: dict, overrides: dict) -> dict:
        return {**revision_meta, **{k: v["value"] for k, v in overrides.items()}}

    def _commit(self, import_id):
        """Commit a prepared import under its original precondition, or mark it conflicted."""
        for _ in range(MAX_COMMIT_ATTEMPTS):
            imp = self.tables.get(IMPORTS, "imp:" + import_id)
            if imp is None or imp["state"] != "prepared":
                return
            document_id, revision_id = imp["document_id"], imp["revision_id"]
            ev = self.tables.get(DOCS, "evrev:" + revision_id)
            if ev:                                                  # committed; the import row lagged
                return self._mark_committed(imp, ev)
            rev = self.tables.get(_revisions(document_id), "rev:" + revision_id)
            doc = self.tables.get(DOCS, "doc:" + document_id)
            state = self.tables.get(DOCS, "state")
            expected = imp["expected_etag"] or None
            stream = _stream_key(rev["asset_id"], rev["document_type"], rev["environment_key"], rev["scope_key"])
            stale = (doc["doc_etag"] != expected) if doc else (expected is not None)
            clash = doc is None and self.tables.get(DOCS, stream) is not None
            if stale or clash:
                return self._mark_conflicted(imp, rev)
            seq, now, etag = state["sequence"] + 1, self.now(), uuid.uuid4().hex
            revision_meta = {"title": rev["title"], "description": rev["description"], "tags": json.loads(rev["tags"]),
                             "business_area": rev["business_area"], "owner": rev["owner"]}
            overrides = json.loads(doc["overrides"]) if doc else {}
            eff = self._effective(revision_meta, overrides)
            doc_row = {"RowKey": "doc:" + document_id, "document_id": document_id,
                       "document_type": rev["document_type"], "asset_id": rev["asset_id"],
                       "environment_key": rev["environment_key"], "scope_key": rev["scope_key"],
                       "title": eff["title"], "description": eff["description"], "tags": json.dumps(eff["tags"]),
                       "business_area": eff["business_area"], "environment": rev["environment"],
                       "owner": eff["owner"], "revision_metadata": json.dumps(revision_meta),
                       "overrides": json.dumps(overrides), "current_revision_id": revision_id,
                       "archived": doc["archived"] if doc else False,
                       "created_at": doc["created_at"] if doc else now, "updated_at": now, "doc_etag": etag}
            ops = [("replace", {"RowKey": "state", "sequence": seq}, state["etag"]),
                   ("replace", doc_row, doc["etag"]) if doc else ("create", doc_row)]
            if doc is None:
                ops.append(("create", {"RowKey": stream, "document_id": document_id}))
            event = {"kind": "publish", "document_id": document_id, "revision_id": revision_id,
                     "stored_artifact_sha256": rev["stored_artifact_sha256"], "subject": imp["subject"],
                     "occurred_at": now, "sequence": seq, "import_id": import_id}
            ops += [("create", {"RowKey": f"event:{seq:012d}", **event}),
                    ("create", {"RowKey": "evrev:" + revision_id, **event}),
                    ("create", {"RowKey": f"docev:{document_id}:{seq:012d}", **event})]
            self._fault("before_commit")
            try:
                self.tables.transact(DOCS, ops)
            except Conflict:
                continue                                  # another commit moved the state or the document
            self._fault("after_commit")
            return self._mark_committed(imp, self.tables.get(DOCS, "evrev:" + revision_id))
        raise LibraryError("COMMIT_CONTENDED", "the catalogue was busy; retry with the same key", 503)

    def _mark_committed(self, imp, ev):
        rev_part, rev_key = _revisions(imp["document_id"]), "rev:" + imp["revision_id"]
        rev = self.tables.get(rev_part, rev_key)
        if rev and rev["status"] != "committed":
            rev.update(status="committed", published_at=ev["occurred_at"])
            self.tables.transact(rev_part, [("upsert", rev)])
        self._update_import(imp["import_id"], state="committed", catalogue_sequence=ev["sequence"],
                            committed_event_id=ev["sequence"], completed_at=self.now())

    def _mark_conflicted(self, imp, rev):
        if rev:
            rev["status"] = "conflicted"
            self.tables.transact(_revisions(imp["document_id"]), [("upsert", rev)])
        self._update_import(imp["import_id"], state="conflicted", error_code="REVISION_CONFLICT",
                            error_message="the document changed before this revision could be published",
                            completed_at=self.now())

    def _fail(self, import_id, exc: LibraryError):
        row = self.tables.get(IMPORTS, "imp:" + import_id)
        if row and row["state"] == "validating":
            self._update_import(import_id, state="failed", error_code=exc.code, error_message=exc.message[:1000],
                                completed_at=self.now())

    def _replay(self, import_id) -> dict:
        imp = self.get_import(import_id)
        if imp["state"] == "committed":
            return imp
        if imp["state"] == "conflicted":
            raise conflict("REVISION_CONFLICT", imp["error_message"] or "revision conflicted", import_id=import_id)
        if imp["state"] == "failed":
            raise LibraryError(imp["error_code"], imp["error_message"], REPLAY_STATUS.get(imp["error_code"], 409),
                               {"import_id": import_id})
        raise LibraryError("IMPORT_INCOMPLETE", "the import did not complete; retry with the same key", 409,
                           {"import_id": import_id, "state": imp["state"]})

    def reconcile(self, *, startup: bool = False) -> dict:
        """Finish or conflict prepared imports; at startup also retire old interrupted ones."""
        report = {"committed": 0, "conflicted": 0, "interrupted": 0, "orphans_removed": 0}
        imports = sorted(self.tables.query(IMPORTS, "imp:"), key=lambda r: r["created_at"])
        for imp in imports:
            if imp["state"] == "prepared":
                self._commit(imp["import_id"])
                state = self.get_import(imp["import_id"])["state"]
                report["committed" if state == "committed" else "conflicted"] += 1
        if not startup:
            return report
        cutoff = _iso(time.time() - self.cleanup_grace_seconds)
        for imp in imports:
            if imp["state"] != "validating" or imp["created_at"] > cutoff:
                continue
            self._update_import(imp["import_id"], state="failed", error_code="IMPORT_INTERRUPTED",
                                error_message="publication was interrupted before the revision was prepared; "
                                              "retry with a new key", completed_at=self.now())
            report["interrupted"] += 1
            doc, rev = imp.get("document_id"), imp.get("revision_id")
            if doc and rev and self._revision_state(doc, rev)[0] != "committed":
                # Nothing committed this revision: remove its blob and descriptor.
                self.blobs.delete(self.artifact_key(doc, rev))
                descriptor = self.tables.get(_revisions(doc), "rev:" + rev)
                if descriptor:
                    self.tables.transact(_revisions(doc), [("delete", "rev:" + rev, descriptor["etag"])])
                report["orphans_removed"] += 1
        return report

    # ---- reads ------------------------------------------------------------------

    def get_import(self, import_id) -> dict:
        row = self.tables.get(IMPORTS, "imp:" + import_id)
        if row is None:
            raise not_found("import")
        val = lambda k: row.get(k) or None  # noqa: E731
        return {"import_id": row["import_id"], "state": row["state"], "document_id": val("document_id"),
                "revision_id": val("revision_id"), "duplicate": bool(row.get("duplicate_of")),
                "catalogue_sequence": row.get("catalogue_sequence") if row.get("catalogue_sequence") != "" else None,
                "committed_event_id": row.get("committed_event_id") if row.get("committed_event_id") != "" else None,
                "indexing_state": "pending" if row["state"] == "committed" else None,
                "error_code": val("error_code"), "error_message": val("error_message"),
                "created_at": row["created_at"], "completed_at": val("completed_at")}

    @staticmethod
    def _document(row) -> dict:
        return {"document_id": row["document_id"], "document_type": row["document_type"],
                "classification": {"business_area": row["business_area"], "environment": row["environment"],
                                   "owner": row["owner"]},
                "title": row["title"], "description": row["description"], "tags": json.loads(row["tags"]),
                "current_revision_id": row["current_revision_id"], "archived": bool(row["archived"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"], "etag": row["doc_etag"],
                "publication": {"asset_id": row["asset_id"], "environment_key": row["environment_key"],
                                "scope_key": row["scope_key"]}}

    def get_document(self, document_id) -> dict:
        row = self.tables.get(DOCS, "doc:" + str(document_id))
        if row is None:
            raise not_found("document")
        return self._document(row)

    def list_documents(self, *, q=None, document_type=None, business_area=None, environment=None, owner=None,
                       tag=None, archived=False, cursor=None, limit=50) -> dict:
        limit = max(1, min(int(limit), 200))
        docs = [self._document(r) for r in self.tables.query(DOCS, "doc:")]
        docs = [d for d in docs if d["archived"] == bool(archived)
                and (document_type is None or d["document_type"] == document_type)
                and (business_area is None or d["classification"]["business_area"] == business_area)
                and (environment is None or d["classification"]["environment"] == environment)
                and (owner is None or d["classification"]["owner"] == owner)
                and (not q or q.lower() in d["title"].lower())
                and (tag is None or tag in d["tags"])]
        docs.sort(key=lambda d: (d["title"].lower(), d["document_id"]))
        if cursor:
            after = tuple(_cursor_decode(cursor))
            docs = [d for d in docs if (d["title"].lower(), d["document_id"]) > after]
        items = docs[:limit]
        more = len(docs) > limit
        return {"items": items,
                "next_cursor": _cursor_encode([items[-1]["title"].lower(), items[-1]["document_id"]]) if more else None}

    def list_revisions(self, document_id, *, cursor=None, limit=50) -> dict:
        self.get_document(document_id)
        limit = max(1, min(int(limit), 200))
        before = _cursor_decode(cursor)[0] if cursor else None
        events = [e for e in reversed(self.tables.query(DOCS, f"docev:{document_id}:")) if e["kind"] == "publish"
                  and (before is None or e["sequence"] < before)]
        descriptors = {r["revision_id"]: r for r in self.tables.query(_revisions(document_id), "rev:")}
        page = events[:limit]
        items = []
        for e in page:
            r = descriptors[e["revision_id"]]
            items.append({"document_id": document_id, "revision_id": r["revision_id"], "title": r["title"],
                          "schema_version": r["schema_version"], "generated_at": r["generated_at"],
                          "published_at": e["occurred_at"], "publisher_subject": r["publisher_subject"],
                          "artifact_sha256": r["stored_artifact_sha256"], "content_sha256": r["content_sha256"],
                          "size_bytes": r["size_bytes"], "section_count": r["section_count"],
                          "catalogue_sequence": e["sequence"]})
        more = len(events) > limit
        return {"items": items, "next_cursor": _cursor_encode([page[-1]["sequence"]]) if more else None}

    def read_artifact(self, document_id, revision_id) -> bytes:
        rev = self.tables.get(_revisions(document_id), "rev:" + revision_id)
        if rev is None or self._revision_state(document_id, revision_id)[0] != "committed":
            raise not_found("revision")
        try:
            data = self.blobs.read(rev["artifact_key"])
        except NotFound:
            raise LibraryError("ARTIFACT_CORRUPT", "stored artifact is missing", 500) from None
        if _sha256(data) != rev["stored_artifact_sha256"]:
            raise LibraryError("ARTIFACT_CORRUPT", "stored artifact failed its integrity check", 500)
        return data

    def catalogue_sequence(self) -> int:
        return self.tables.get(DOCS, "state")["sequence"]

    # ---- catalogue changes that advance the sequence -------------------------------

    def _catalogue_change(self, document_id, expected_etag, change, extra_rows=lambda seq, doc, now: []):
        """Apply `change(doc_row, now, seq)` with the document ETag precondition, in one commit."""
        if not expected_etag:
            raise LibraryError("PRECONDITION_REQUIRED", "If-Match is required", 428)
        for _ in range(MAX_COMMIT_ATTEMPTS):
            doc = self.tables.get(DOCS, "doc:" + str(document_id))
            if doc is None:
                raise not_found("document")
            if doc["doc_etag"] != expected_etag:
                raise conflict("REVISION_CONFLICT", "the document changed since it was read", current_etag=doc["doc_etag"])
            state = self.tables.get(DOCS, "state")
            seq, now = state["sequence"] + 1, self.now()
            new = change(dict(doc), now, seq)
            new.update(updated_at=now, doc_etag=uuid.uuid4().hex)
            ops = [("replace", {"RowKey": "state", "sequence": seq}, state["etag"]),
                   ("replace", new, doc["etag"])] + extra_rows(seq, doc, now)
            try:
                self.tables.transact(DOCS, ops)
                return seq
            except Conflict:
                continue
        raise LibraryError("COMMIT_CONTENDED", "the catalogue was busy; retry", 503)

    def _set_archived(self, document_id, expected_etag, subject, archived: bool) -> dict:
        kind = "archive" if archived else "restore"

        def events(seq, doc, now):
            e = {"kind": kind, "document_id": str(document_id), "revision_id": "", "subject": subject,
                 "occurred_at": now, "sequence": seq}
            return [("create", {"RowKey": f"event:{seq:012d}", **e}),
                    ("create", {"RowKey": f"docev:{document_id}:{seq:012d}", **e})]
        self._catalogue_change(document_id, expected_etag, lambda d, now, seq: {**d, "archived": archived}, events)
        return self.get_document(document_id)

    def archive(self, document_id, expected_etag, subject) -> dict:
        return self._set_archived(document_id, expected_etag, subject, True)

    def restore(self, document_id, expected_etag, subject) -> dict:
        return self._set_archived(document_id, expected_etag, subject, False)

    # ---- metadata overrides (A40) ----------------------------------------------------

    def get_metadata(self, document_id) -> dict:
        row = self.tables.get(DOCS, "doc:" + str(document_id))
        if row is None:
            raise not_found("document")
        revision_meta, overrides = json.loads(row["revision_metadata"]), json.loads(row["overrides"])
        return {"document_id": row["document_id"], "revision_id": row["current_revision_id"],
                "effective": self._effective(revision_meta, overrides), "revision": revision_meta,
                "overrides": dict(sorted(overrides.items())),
                "immutable": {"environment": row["environment"], "environment_key": row["environment_key"],
                              "scope_key": row["scope_key"], "asset_id": row["asset_id"]},
                "etag": row["doc_etag"], "catalogue_sequence": self.catalogue_sequence()}

    def set_metadata(self, document_id, changes: dict, reason, expected_etag, subject) -> dict:
        if not expected_etag:
            raise LibraryError("PRECONDITION_REQUIRED", "If-Match is required", 428)
        changes = validate_overrides(changes)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise LibraryError("INVALID_REQUEST", "a reason of 1 to 1000 characters is required")
        audit = {}

        def change(doc, now, seq):
            revision_meta, overrides = json.loads(doc["revision_metadata"]), json.loads(doc["overrides"])
            before = self._effective(revision_meta, overrides)
            for field, value in changes.items():
                if value is None:
                    overrides.pop(field, None)
                else:
                    overrides[field] = {"value": value, "subject": subject, "reason": reason.strip(),
                                        "set_at": now, "catalogue_sequence": seq}
            after = self._effective(revision_meta, overrides)
            audit.update(before=before, after=after)
            return {**doc, "overrides": json.dumps(overrides), "title": after["title"],
                    "description": after["description"], "tags": json.dumps(after["tags"]),
                    "business_area": after["business_area"], "owner": after["owner"]}

        def rows(seq, doc, now):
            return [("create", {"RowKey": f"meta:{document_id}:{seq:012d}", "catalogue_sequence": seq,
                                "subject": subject, "reason": reason.strip(), "changes": json.dumps(changes),
                                "before": json.dumps(audit["before"]), "after": json.dumps(audit["after"]),
                                "occurred_at": now})]
        self._catalogue_change(document_id, expected_etag, change, rows)
        return self.get_metadata(document_id)

    def metadata_history(self, document_id) -> list[dict]:
        self.get_document(document_id)
        return [{"catalogue_sequence": r["catalogue_sequence"], "subject": r["subject"], "reason": r["reason"],
                 "changes": json.loads(r["changes"]), "before": json.loads(r["before"]),
                 "after": json.loads(r["after"]), "occurred_at": r["occurred_at"]}
                for r in self.tables.query(DOCS, f"meta:{document_id}:")]

    # ---- repository for derived state and manual links (repository.py) ---------------

    def read_sequence(self) -> int:
        return self.catalogue_sequence()

    def catalogue_documents(self) -> list[dict]:
        return [{"document_id": r["document_id"], "document_type": r["document_type"],
                 "current_revision_id": r["current_revision_id"], "archived": bool(r["archived"]),
                 "title": r["title"], "tags": json.loads(r["tags"]), "business_area": r["business_area"],
                 "environment": r["environment"], "owner": r["owner"], "environment_key": r["environment_key"]}
                for r in self.tables.query(DOCS, "doc:")]

    def is_committed(self, document_id, revision_id) -> bool:
        ev = self.tables.get(DOCS, "evrev:" + str(revision_id))
        return bool(ev) and ev["document_id"] == str(document_id)

    def derived_state(self) -> dict:
        out = {}
        for name in ("search", "relationships"):
            row = self.tables.get(DOCS, "derived:" + name)
            out[name] = {k: (row.get(k) or None) if k == "snapshot_key" else row.get(k)
                         for k in ("generation_sequence", "state", "snapshot_key", "updated_at")} if row else None
        return out

    def put_derived_blob(self, key: str, data: bytes) -> None:
        if not self.blobs.create(key, data) and self.blobs.read(key) != data:
            raise LibraryError("INTERNAL_ERROR", "derived snapshot key collision", 500)

    def get_derived_blob(self, key: str) -> bytes:
        return self.blobs.read(key)

    def save_generation(self, g: dict, now: str) -> None:
        """The immutable generation record (blob) and its per-revision index rows. Nothing
        points at it until switch_derived commits its `gen:` row."""
        record = {k: g[k] for k in ("generation_id", "catalogue_sequence", "rule_version", "members",
                                    "detected", "manual")}
        record["completed_at"] = now
        self.put_derived_blob(f"derived/generations/{g['generation_id']}.json",
                              json.dumps(record, separators=(",", ":")).encode("utf-8"))
        rows = [("upsert", {"RowKey": f"rev:{rev}:{FAR - g['catalogue_sequence']:012d}:{g['generation_id']}",
                            "generation_id": g["generation_id"]}) for rev in sorted(g["members"].values())]
        for i in range(0, len(rows), 100):
            self.tables.transact(GENERATIONS, rows[i:i + 100])

    def switch_derived(self, seq, search_key, g, now) -> bool:
        state = self.tables.get(DOCS, "state")
        if state["sequence"] != seq:
            return False
        pointer = lambda name, key: ("upsert", {"RowKey": "derived:" + name, "generation_sequence": seq,  # noqa: E731
                                                "state": "ready", "snapshot_key": key, "updated_at": now})
        try:
            # Re-writing the state row under its ETag proves the sequence did not move.
            self.tables.transact(DOCS, [("replace", {"RowKey": "state", "sequence": seq}, state["etag"]),
                                        pointer("search", search_key), pointer("relationships", g["generation_id"]),
                                        ("create", {"RowKey": "gen:" + g["generation_id"], "catalogue_sequence": seq,
                                                    "rule_version": g["rule_version"], "completed_at": now})])
            return True
        except Conflict:
            return False

    def mark_derived_failed(self, seq) -> None:
        for name in ("search", "relationships"):
            row = self.tables.get(DOCS, "derived:" + name) or {"RowKey": "derived:" + name,
                                                              "generation_sequence": seq, "snapshot_key": ""}
            row.update(state="failed", updated_at=self.now())
            self.tables.transact(DOCS, [("upsert", row)])

    def get_generation(self, generation_id) -> dict | None:
        if not self.tables.get(DOCS, "gen:" + str(generation_id)):
            return None                                  # never switched in: not a ready generation
        return json.loads(self.blobs.read(f"derived/generations/{generation_id}.json").decode("utf-8"))

    def latest_generation_for(self, revision_id) -> dict | None:
        for row in self.tables.query(GENERATIONS, f"rev:{revision_id}:"):     # newest first
            g = self.get_generation(row["generation_id"])
            if g is not None:
                return g
        return None

    @staticmethod
    def _manual(row) -> dict:
        return json.loads(row["record"])

    def manual_active(self) -> list[dict]:
        return [r for r in (self._manual(x) for x in self.tables.query(DOCS, "manual:")) if r["status"] != "deleted"]

    def manual_get(self, relationship_id) -> dict | None:
        row = self.tables.get(DOCS, "manual:" + str(relationship_id))
        record = self._manual(row) if row else None
        return record if record and record["status"] != "deleted" else None

    def manual_write(self, observed_seq, record, expected_etag, action, subject, before) -> None:
        state = self.tables.get(DOCS, "state")
        if state["sequence"] != observed_seq:
            raise StaleSequence()
        rid = record["relationship_id"]
        row = self.tables.get(DOCS, "manual:" + rid)
        current = self._manual(row) if row else None
        if expected_etag is not None and (current is None or current["status"] == "deleted"
                                          or current["etag"] != expected_etag):
            raise conflict("REVISION_CONFLICT", "the link changed since it was read",
                           current_etag=current["etag"] if current else None)
        seq = observed_seq + 1
        entity = {"RowKey": "manual:" + rid, "record": json.dumps(record)}
        audit = {"RowKey": f"maudit:{rid}:{seq:012d}", "audit_id": seq, "relationship_id": rid, "action": action,
                 "subject": subject, "occurred_at": self.now(), "before": json.dumps(before) if before else "",
                 "after": json.dumps(record) if action != "delete" else ""}
        try:
            self.tables.transact(DOCS, [("replace", {"RowKey": "state", "sequence": seq}, state["etag"]),
                                        ("replace", entity, row["etag"]) if row else ("create", entity),
                                        ("create", audit)])
        except Conflict:
            raise StaleSequence() from None

    def manual_audit(self, relationship_id) -> list[dict]:
        return [{"audit_id": r["audit_id"], "relationship_id": r["relationship_id"], "action": r["action"],
                 "subject": r["subject"], "occurred_at": r["occurred_at"], "before": r["before"] or None,
                 "after": r["after"] or None} for r in self.tables.query(DOCS, f"maudit:{relationship_id}:")]

    # ---- operations -------------------------------------------------------------------

    def validate_stored(self, document_id, revision_id) -> dict:
        return validate_artifact(self.read_artifact(document_id, revision_id), limits=self.limits)
