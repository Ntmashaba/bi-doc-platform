"""Local catalogue and artifact store with crash-safe publication (handoff 6 and 17.3).

Layout under the data directory:
    catalogue.sqlite3                      catalogue (one application instance)
    artifacts/{document_id}/{revision_id}/document.html   immutable stored artifacts
    staging/                               temporary files; never read by readers

Publication: validate -> re-project into a safe stored artifact -> write it
create-if-absent -> record a prepared revision -> one SQLite transaction commits the
document pointer, publication event and catalogue sequence under the original ETag
precondition. A crash leaves at most a prepared revision; `reconcile()` (run on
open) commits it under its original precondition or marks it conflicted. Readers
only follow committed revisions.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from bidoc_contracts import ContractError, Limits, validate_artifact
from bidoc_engines.convert import UnsupportedProjection, reproject

from .errors import LibraryError, conflict, not_found
from .migrations import migrate


class SimulatedCrash(Exception):
    """Raised by fault injection in tests to stop publication at a named point."""


OVERRIDABLE = ("title", "description", "tags", "business_area", "owner")
_TRIMMED = re.compile(r"^\S(.*\S)?$", re.S)


def validate_overrides(changes) -> dict:
    """Field rules mirror envelope v1; environment and scope are immutable stream fields."""
    if not isinstance(changes, dict) or not changes:
        raise LibraryError("INVALID_REQUEST", "give at least one metadata field to change")
    immutable = sorted(set(changes) & {"environment", "environment_key", "scope_key", "asset_id", "document_type"})
    if immutable:
        raise LibraryError("IMMUTABLE_FIELD", f"{', '.join(immutable)} cannot be changed through metadata", 422,
                           {"fields": immutable})
    unknown = sorted(set(changes) - set(OVERRIDABLE))
    if unknown:
        raise LibraryError("INVALID_REQUEST", f"unknown metadata fields: {', '.join(unknown)}")

    def text(field, value, lo, hi, trimmed=False):
        if not isinstance(value, str) or not lo <= len(value) <= hi or (trimmed and not _TRIMMED.match(value)):
            raise LibraryError("INVALID_REQUEST", f"{field} must be text of {lo} to {hi} characters"
                               + (" without surrounding spaces" if trimmed else ""))
    for field, value in changes.items():
        if value is None:
            continue
        if field == "title":
            text(field, value, 1, 200, True)
        elif field == "description":
            text(field, value, 0, 2000)
        elif field == "tags":
            if not isinstance(value, list) or len(value) > 20 or len(set(map(str, value))) != len(value):
                raise LibraryError("INVALID_REQUEST", "tags must be up to 20 distinct values")
            for tag in value:
                text("each tag", tag, 1, 50, True)
        else:
            text(field, value, 0, 200)
    return changes


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cursor_encode(values) -> str:
    return base64.urlsafe_b64encode(json.dumps(values).encode()).decode()


def _cursor_decode(cursor):
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, TypeError):
        raise LibraryError("INVALID_REQUEST", "invalid cursor") from None


class LocalStore:
    def __init__(self, data_dir, *, limits: Limits = Limits(), faults=(), clock=_now,
                 cleanup_grace_seconds: float = 3600):
        self.root = Path(data_dir)
        self.artifacts = self.root / "artifacts"
        self.staging = self.root / "staging"
        for d in (self.root, self.artifacts, self.staging):
            d.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "catalogue.sqlite3"
        self.limits, self.faults, self.now = limits, set(faults), clock
        # Startup cleanup only touches work older than this, so a second process opening
        # the same folder can never remove a publication that is still in flight.
        self.cleanup_grace_seconds = cleanup_grace_seconds
        with self._db() as conn:
            migrate(conn, self.now())
        self.reconcile(startup=True)

    # ---- infrastructure ---------------------------------------------------------

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self, conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _fault(self, name):
        if name in self.faults:
            raise SimulatedCrash(name)

    def _artifact_path(self, document_id, revision_id) -> Path:
        return self.artifacts / document_id / revision_id / "document.html"

    def _write_immutable(self, path: Path, data: bytes, import_id: str) -> None:
        """Create-if-absent: an existing artifact is never overwritten."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.staging / f"{import_id}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)                     # atomic, fails if the target exists
        except FileExistsError:
            if path.read_bytes() != data:
                raise conflict("REVISION_BYTES_CONFLICT", "a different artifact is already stored for this revision")
        finally:
            tmp.unlink(missing_ok=True)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    # ---- publication ------------------------------------------------------------

    def publish(self, data: bytes, *, subject: str, idempotency_key: str, expected_etag: str | None = None,
                target_document_id: str | None = None, query_code: str = "withheld") -> dict:
        if not idempotency_key or len(idempotency_key) > 200:
            raise LibraryError("INVALID_REQUEST", "an Idempotency-Key of 1-200 characters is required")
        if query_code not in ("withheld", "included"):
            raise LibraryError("INVALID_REQUEST", "query_code must be withheld or included")
        if len(data) > self.limits.html_bytes:
            raise LibraryError("PAYLOAD_TOO_LARGE", f"the document exceeds the {self.limits.html_bytes}-byte limit", 413)
        submitted = _sha256(data)
        request = _sha256(json.dumps([submitted, target_document_id, expected_etag, query_code]).encode())

        with self._db() as conn:
            existing = conn.execute("SELECT * FROM imports WHERE subject=? AND idempotency_key=?",
                                    (subject, idempotency_key)).fetchone()
            if existing:
                if existing["request_sha256"] != request:
                    raise conflict("IDEMPOTENCY_KEY_REUSED", "this Idempotency-Key was used for a different request")
                if existing["state"] in ("prepared", "validating"):
                    self.reconcile()
                return self._replay(existing["import_id"])
            duplicate = conn.execute("SELECT * FROM revisions WHERE submitted_artifact_sha256=? "
                                     "ORDER BY status='committed' DESC LIMIT 1", (submitted,)).fetchone()
            import_id = str(uuid.uuid4())
            with self._tx(conn):
                conn.execute("INSERT INTO imports (import_id, subject, idempotency_key, request_sha256, "
                             "submitted_artifact_sha256, target_document_id, expected_etag, state, created_at) "
                             "VALUES (?,?,?,?,?,?,?, 'validating', ?)",
                             (import_id, subject, idempotency_key, request, submitted, target_document_id,
                              expected_etag, self.now()))
        if duplicate is not None:
            return self._record_duplicate(import_id, duplicate)
        try:
            return self._publish(import_id, data, subject, expected_etag, target_document_id, query_code, submitted)
        except LibraryError as exc:
            self._fail(import_id, exc)
            raise

    def _record_duplicate(self, import_id, revision) -> dict:
        """Identical bytes: recover the original outcome without changing the current pointer."""
        with self._db() as conn, self._tx(conn):
            origin = conn.execute("SELECT * FROM imports WHERE revision_id=? AND duplicate_of IS NULL "
                                  "ORDER BY created_at LIMIT 1", (revision["revision_id"],)).fetchone()
            state = {"committed": "committed", "conflicted": "conflicted"}.get(revision["status"], "prepared")
            conn.execute("UPDATE imports SET state=?, document_id=?, revision_id=?, duplicate_of=?, "
                         "committed_event_id=?, catalogue_sequence=?, completed_at=? WHERE import_id=?",
                         (state, revision["document_id"], revision["revision_id"],
                          origin["import_id"] if origin else None,
                          origin["committed_event_id"] if origin else None,
                          origin["catalogue_sequence"] if origin else None, self.now(), import_id))
        if state == "prepared":
            self.reconcile()
        return self._replay(import_id)

    def _publish(self, import_id, data, subject, expected_etag, target_document_id, query_code, submitted):
        try:
            manifest = validate_artifact(data, limits=self.limits)
        except ContractError as exc:
            raise LibraryError(exc.code if exc.code in ("ARTIFACT_TOO_LARGE",) else "CONTRACT_INVALID",
                               str(exc), 413 if exc.code == "ARTIFACT_TOO_LARGE" else 422,
                               {"contract_code": exc.code, "issues": exc.issues[:20]}) from None
        document_id, revision_id = manifest["document_id"], manifest["revision_id"]
        if target_document_id and target_document_id != document_id:
            raise LibraryError("INVALID_REQUEST", "target_document_id does not match the artifact's document_id")
        with self._db() as conn:
            self._check_identity(conn, manifest, expected_etag)
            other = conn.execute("SELECT submitted_artifact_sha256 FROM revisions WHERE revision_id=?",
                                 (revision_id,)).fetchone()
            if other and other[0] != submitted:
                raise conflict("REVISION_BYTES_CONFLICT", "this revision ID was already published with different bytes")
        try:
            stored, stored_manifest = reproject(manifest, query_code=query_code)
        except UnsupportedProjection as exc:
            raise LibraryError("UNSUPPORTED_SAFE_PROJECTION", str(exc), 422) from None
        except ContractError as exc:
            raise LibraryError("CONTRACT_INVALID", f"conversion failed: {exc}", 422) from None

        path = self._artifact_path(document_id, revision_id)
        self._write_immutable(path, stored, import_id)
        self._fault("after_artifact_write")
        with self._db() as conn, self._tx(conn):
            conn.execute("INSERT INTO revisions (document_id, revision_id, document_type, schema_version, "
                         "generated_at, publisher_subject, artifact_key, submitted_artifact_sha256, "
                         "stored_artifact_sha256, content_sha256, size_bytes, section_count, title, status) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'prepared')",
                         (document_id, revision_id, manifest["document_type"], manifest["schema_version"],
                          manifest["generated_at"], subject, str(path.relative_to(self.root)), submitted,
                          _sha256(stored), stored_manifest["content_sha256"], len(stored),
                          len(stored_manifest["sections"]), stored_manifest["title"]))
            conn.execute("UPDATE imports SET state='prepared', document_id=?, revision_id=? WHERE import_id=?",
                         (document_id, revision_id, import_id))
        self._fault("after_prepare")
        self._commit(import_id, stored_manifest)
        return self._replay(import_id)

    def _check_identity(self, conn, manifest, expected_etag):
        doc = conn.execute("SELECT * FROM documents WHERE document_id=?", (manifest["document_id"],)).fetchone()
        pub = manifest["publication"]
        if doc is None:
            if expected_etag is not None:
                raise conflict("REVISION_CONFLICT", "If-Match was given but the document does not exist yet")
            clash = conn.execute("SELECT document_id FROM documents WHERE asset_id=? AND document_type=? AND "
                                 "environment_key=? AND scope_key=?",
                                 (pub["asset_id"], manifest["document_type"], pub["environment_key"],
                                  pub["scope_key"])).fetchone()
            if clash:
                raise conflict("STREAM_IDENTITY_CONFLICT", "another document already publishes this stream",
                               document_id=clash[0])
            return None
        if doc["document_type"] != manifest["document_type"]:
            raise conflict("DOCUMENT_TYPE_CONFLICT", "a document ID cannot change type")
        if (doc["asset_id"], doc["environment_key"], doc["scope_key"]) != \
                (pub["asset_id"], pub["environment_key"], pub["scope_key"]):
            raise conflict("STREAM_IDENTITY_CONFLICT", "asset, environment and scope of a document are immutable")
        if expected_etag is None:
            raise LibraryError("PRECONDITION_REQUIRED", "updating an existing document requires If-Match", 428,
                               {"current_etag": doc["etag"]})
        if expected_etag != doc["etag"]:
            raise conflict("REVISION_CONFLICT", "the document changed since it was read", current_etag=doc["etag"])
        return doc

    def _commit(self, import_id, manifest=None):
        """Commit a prepared import under its original precondition, or mark it conflicted."""
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                imp = conn.execute("SELECT * FROM imports WHERE import_id=?", (import_id,)).fetchone()
                if imp["state"] != "prepared":
                    conn.execute("ROLLBACK")
                    return
                rev = conn.execute("SELECT * FROM revisions WHERE revision_id=?", (imp["revision_id"],)).fetchone()
                if manifest is None:
                    manifest = validate_artifact((self.root / rev["artifact_key"]).read_bytes(), limits=self.limits)
                doc = conn.execute("SELECT * FROM documents WHERE document_id=?", (rev["document_id"],)).fetchone()
                pub = manifest["publication"]
                stale = (doc["etag"] != imp["expected_etag"]) if doc else (imp["expected_etag"] is not None)
                clash = None if doc else conn.execute(
                    "SELECT 1 FROM documents WHERE asset_id=? AND document_type=? AND environment_key=? AND scope_key=?",
                    (pub["asset_id"], manifest["document_type"], pub["environment_key"], pub["scope_key"])).fetchone()
                if stale or clash:
                    conn.execute("UPDATE revisions SET status='conflicted' WHERE revision_id=?", (rev["revision_id"],))
                    conn.execute("UPDATE imports SET state='conflicted', error_code='REVISION_CONFLICT', "
                                 "error_message='the document changed before this revision could be published', "
                                 "completed_at=? WHERE import_id=?", (self.now(), import_id))
                    conn.execute("COMMIT")
                    return
                now = self.now()
                seq = conn.execute("UPDATE catalogue_state SET sequence = sequence + 1 WHERE id=1 "
                                   "RETURNING sequence").fetchone()[0]
                cls, etag = manifest["classification"], uuid.uuid4().hex
                revision_meta = {"title": manifest["title"], "description": manifest["description"],
                                 "tags": manifest["tags"], "business_area": cls["business_area"],
                                 "owner": cls["owner"]}
                eff = self._effective(conn, rev["document_id"], revision_meta)   # overrides survive new revisions
                values = (eff["title"], eff["description"], json.dumps(eff["tags"]), eff["business_area"],
                          cls["environment"], eff["owner"], json.dumps(revision_meta), rev["revision_id"], now, etag)
                if doc is None:
                    conn.execute("INSERT INTO documents (document_id, document_type, asset_id, environment_key, "
                                 "scope_key, title, description, tags, business_area, environment, owner, "
                                 "revision_metadata, current_revision_id, created_at, updated_at, etag) "
                                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (rev["document_id"], rev["document_type"], pub["asset_id"], pub["environment_key"],
                                  pub["scope_key"], *values[:8], now, now, etag))
                else:
                    conn.execute("UPDATE documents SET title=?, description=?, tags=?, business_area=?, "
                                 "environment=?, owner=?, revision_metadata=?, current_revision_id=?, updated_at=?, "
                                 "etag=? WHERE document_id=?", (*values, rev["document_id"]))
                event = conn.execute("INSERT INTO publication_events (catalogue_sequence, kind, document_id, "
                                     "revision_id, stored_artifact_sha256, subject, occurred_at) "
                                     "VALUES (?, 'publish', ?, ?, ?, ?, ?)",
                                     (seq, rev["document_id"], rev["revision_id"], rev["stored_artifact_sha256"],
                                      imp["subject"], now)).lastrowid
                conn.execute("UPDATE revisions SET status='committed', published_at=? WHERE revision_id=?",
                             (now, rev["revision_id"]))
                conn.execute("UPDATE imports SET state='committed', committed_event_id=?, catalogue_sequence=?, "
                             "completed_at=? WHERE import_id=?", (event, seq, now, import_id))
                self._fault("before_commit")
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        self._fault("after_commit")

    def _fail(self, import_id, exc: LibraryError):
        with self._db() as conn, self._tx(conn):
            conn.execute("UPDATE imports SET state='failed', error_code=?, error_message=?, completed_at=? "
                         "WHERE import_id=? AND state='validating'", (exc.code, exc.message[:1000], self.now(), import_id))

    def _replay(self, import_id) -> dict:
        """The durable outcome of an import, raised as an error when it did not publish."""
        imp = self.get_import(import_id)
        if imp["state"] == "committed":
            return imp
        if imp["state"] == "conflicted":
            raise conflict("REVISION_CONFLICT", imp["error_message"] or "revision conflicted", import_id=import_id)
        if imp["state"] == "failed":
            status = {"CONTRACT_INVALID": 422, "UNSUPPORTED_SAFE_PROJECTION": 422, "PAYLOAD_TOO_LARGE": 413,
                      "ARTIFACT_TOO_LARGE": 413, "PRECONDITION_REQUIRED": 428, "INVALID_REQUEST": 400}.get(
                          imp["error_code"], 409)
            raise LibraryError(imp["error_code"], imp["error_message"], status, {"import_id": import_id})
        raise LibraryError("IMPORT_INCOMPLETE", "the import did not complete; retry with the same key", 409,
                           {"import_id": import_id, "state": imp["state"]})

    def reconcile(self, *, startup: bool = False) -> dict:
        """Finish or conflict prepared imports under their original preconditions.

        With startup=True (when opening the store) also fail imports interrupted before
        they were prepared and remove unreferenced artifact and staging files, but only
        those older than the cleanup grace period: a publication in flight in another
        process is always younger than that.
        """
        report = {"committed": 0, "conflicted": 0, "interrupted": 0, "orphans_removed": 0}
        with self._db() as conn:
            prepared = [r[0] for r in conn.execute("SELECT import_id FROM imports WHERE state='prepared' "
                                                   "ORDER BY created_at")]
        for import_id in prepared:
            self._commit(import_id)
            state = self.get_import(import_id)["state"]
            report["committed" if state == "committed" else "conflicted"] += 1
        if not startup:
            return report
        cutoff = time.time() - self.cleanup_grace_seconds
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with self._db() as conn, self._tx(conn):
            report["interrupted"] = conn.execute(
                "UPDATE imports SET state='failed', error_code='IMPORT_INTERRUPTED', error_message="
                "'publication was interrupted before the revision was prepared; retry with a new key', "
                "completed_at=? WHERE state='validating' AND created_at <= ?", (self.now(), cutoff_iso)).rowcount
            known = {r[0] for r in conn.execute("SELECT artifact_key FROM revisions")}
        for path in self.artifacts.glob("*/*/document.html"):
            if str(path.relative_to(self.root)) not in known and path.stat().st_mtime <= cutoff:
                path.unlink()
                report["orphans_removed"] += 1
        for d in sorted(self.artifacts.glob("*/*"), reverse=True) + sorted(self.artifacts.glob("*")):
            if d.is_dir() and not any(d.iterdir()) and d.stat().st_mtime <= cutoff:
                d.rmdir()
        for tmp in self.staging.glob("*"):
            if tmp.stat().st_mtime <= cutoff:
                tmp.unlink()
        return report

    # ---- reads ------------------------------------------------------------------

    def get_import(self, import_id) -> dict:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM imports WHERE import_id=?", (import_id,)).fetchone()
        if row is None:
            raise not_found("import")
        return {"import_id": row["import_id"], "state": row["state"], "document_id": row["document_id"],
                "revision_id": row["revision_id"], "duplicate": row["duplicate_of"] is not None,
                "catalogue_sequence": row["catalogue_sequence"], "committed_event_id": row["committed_event_id"],
                "indexing_state": "pending" if row["state"] == "committed" else None,
                "error_code": row["error_code"], "error_message": row["error_message"],
                "created_at": row["created_at"], "completed_at": row["completed_at"]}

    @staticmethod
    def _document(row) -> dict:
        return {"document_id": row["document_id"], "document_type": row["document_type"],
                "classification": {"business_area": row["business_area"], "environment": row["environment"],
                                   "owner": row["owner"]},
                "title": row["title"], "description": row["description"], "tags": json.loads(row["tags"]),
                "current_revision_id": row["current_revision_id"], "archived": bool(row["archived"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"], "etag": row["etag"],
                "publication": {"asset_id": row["asset_id"], "environment_key": row["environment_key"],
                                "scope_key": row["scope_key"]}}

    def get_document(self, document_id) -> dict:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
        if row is None:
            raise not_found("document")
        return self._document(row)

    def list_documents(self, *, q=None, document_type=None, business_area=None, environment=None, owner=None,
                       tag=None, archived=False, cursor=None, limit=50) -> dict:
        limit = max(1, min(int(limit), 200))
        where, args = ["archived=?"], [1 if archived else 0]
        for column, value in (("document_type", document_type), ("business_area", business_area),
                              ("environment", environment), ("owner", owner)):
            if value is not None:
                where.append(f"{column}=?")
                args.append(value)
        if q:
            where.append("instr(lower(title), ?) > 0")
            args.append(q.lower())
        if tag is not None:
            where.append("EXISTS (SELECT 1 FROM json_each(documents.tags) WHERE value=?)")
            args.append(tag)
        if cursor:
            title_key, doc_id = _cursor_decode(cursor)
            where.append("(lower(title), document_id) > (?, ?)")
            args += [title_key, doc_id]
        with self._db() as conn:
            rows = conn.execute(f"SELECT * FROM documents WHERE {' AND '.join(where)} "
                                "ORDER BY lower(title), document_id LIMIT ?", (*args, limit + 1)).fetchall()
        items = [self._document(r) for r in rows[:limit]]
        more = len(rows) > limit
        return {"items": items,
                "next_cursor": _cursor_encode([items[-1]["title"].lower(), items[-1]["document_id"]]) if more else None}

    def list_revisions(self, document_id, *, cursor=None, limit=50) -> dict:
        """Committed history, newest first, enumerated from publication events."""
        self.get_document(document_id)
        limit = max(1, min(int(limit), 200))
        before = _cursor_decode(cursor)[0] if cursor else None
        with self._db() as conn:
            rows = conn.execute(
                "SELECT e.event_id, e.catalogue_sequence, r.* FROM publication_events e "
                "JOIN revisions r ON r.revision_id = e.revision_id WHERE e.document_id=? AND e.kind='publish' "
                + ("AND e.event_id < ? " if before is not None else "") + "ORDER BY e.event_id DESC LIMIT ?",
                (document_id, *([before] if before is not None else []), limit + 1)).fetchall()
        items = [{"document_id": r["document_id"], "revision_id": r["revision_id"], "title": r["title"],
                  "schema_version": r["schema_version"], "generated_at": r["generated_at"],
                  "published_at": r["published_at"], "publisher_subject": r["publisher_subject"],
                  "artifact_sha256": r["stored_artifact_sha256"], "content_sha256": r["content_sha256"],
                  "size_bytes": r["size_bytes"], "section_count": r["section_count"],
                  "catalogue_sequence": r["catalogue_sequence"]} for r in rows[:limit]]
        more = len(rows) > limit
        return {"items": items, "next_cursor": _cursor_encode([rows[limit - 1]["event_id"]]) if more else None}

    def read_artifact(self, document_id, revision_id) -> bytes:
        """Stored bytes of a committed revision, integrity-checked."""
        with self._db() as conn:
            row = conn.execute("SELECT * FROM revisions WHERE document_id=? AND revision_id=? AND status='committed'",
                               (document_id, revision_id)).fetchone()
        if row is None:
            raise not_found("revision")
        data = (self.root / row["artifact_key"]).read_bytes()
        if _sha256(data) != row["stored_artifact_sha256"]:
            raise LibraryError("ARTIFACT_CORRUPT", "stored artifact failed its integrity check", 500)
        return data

    def catalogue_sequence(self) -> int:
        with self._db() as conn:
            return conn.execute("SELECT sequence FROM catalogue_state WHERE id=1").fetchone()[0]

    # ---- archive / restore ------------------------------------------------------

    def _set_archived(self, document_id, expected_etag, subject, archived: bool) -> dict:
        if not expected_etag:
            raise LibraryError("PRECONDITION_REQUIRED", "If-Match is required", 428)
        with self._db() as conn, self._tx(conn):
            doc = conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
            if doc is None:
                raise not_found("document")
            if doc["etag"] != expected_etag:
                raise conflict("REVISION_CONFLICT", "the document changed since it was read", current_etag=doc["etag"])
            now, etag = self.now(), uuid.uuid4().hex
            seq = conn.execute("UPDATE catalogue_state SET sequence = sequence + 1 WHERE id=1 "
                               "RETURNING sequence").fetchone()[0]
            conn.execute("UPDATE documents SET archived=?, updated_at=?, etag=? WHERE document_id=?",
                         (1 if archived else 0, now, etag, document_id))
            conn.execute("INSERT INTO publication_events (catalogue_sequence, kind, document_id, subject, occurred_at) "
                         "VALUES (?,?,?,?,?)", (seq, "archive" if archived else "restore", document_id, subject, now))
        return self.get_document(document_id)

    def archive(self, document_id, expected_etag, subject) -> dict:
        return self._set_archived(document_id, expected_etag, subject, True)

    def restore(self, document_id, expected_etag, subject) -> dict:
        return self._set_archived(document_id, expected_etag, subject, False)

    # ---- metadata overrides (A40) ----------------------------------------------

    @staticmethod
    def _effective(conn, document_id, revision_meta) -> dict:
        eff = dict(revision_meta)
        for row in conn.execute("SELECT field, value FROM metadata_overrides WHERE document_id=?", (document_id,)):
            eff[row["field"]] = json.loads(row["value"])
        return eff

    def get_metadata(self, document_id) -> dict:
        """Effective metadata, the current revision's own metadata, and who overrode what."""
        with self._db() as conn:
            doc = conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
            if doc is None:
                raise not_found("document")
            rows = conn.execute("SELECT * FROM metadata_overrides WHERE document_id=? ORDER BY field",
                                (document_id,)).fetchall()
            seq = conn.execute("SELECT sequence FROM catalogue_state WHERE id=1").fetchone()[0]
        revision_meta = json.loads(doc["revision_metadata"])
        effective = dict(revision_meta)
        overrides = {}
        for r in rows:
            effective[r["field"]] = json.loads(r["value"])
            overrides[r["field"]] = {"value": effective[r["field"]], "subject": r["subject"], "reason": r["reason"],
                                     "set_at": r["set_at"], "catalogue_sequence": r["catalogue_sequence"]}
        return {"document_id": document_id, "revision_id": doc["current_revision_id"], "effective": effective,
                "revision": revision_meta, "overrides": overrides,
                "immutable": {"environment": doc["environment"], "environment_key": doc["environment_key"],
                              "scope_key": doc["scope_key"], "asset_id": doc["asset_id"]},
                "etag": doc["etag"], "catalogue_sequence": seq}

    def set_metadata(self, document_id, changes: dict, reason, expected_etag, subject) -> dict:
        """Apply overrides (None removes one). Artifacts are untouched; the sequence advances."""
        if not expected_etag:
            raise LibraryError("PRECONDITION_REQUIRED", "If-Match is required", 428)
        changes = validate_overrides(changes)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise LibraryError("INVALID_REQUEST", "a reason of 1 to 1000 characters is required")
        with self._db() as conn, self._tx(conn):
            doc = conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
            if doc is None:
                raise not_found("document")
            if doc["etag"] != expected_etag:
                raise conflict("REVISION_CONFLICT", "the document changed since it was read", current_etag=doc["etag"])
            revision_meta = json.loads(doc["revision_metadata"])
            before = self._effective(conn, document_id, revision_meta)
            now, etag = self.now(), uuid.uuid4().hex
            seq = conn.execute("UPDATE catalogue_state SET sequence = sequence + 1 WHERE id=1 "
                               "RETURNING sequence").fetchone()[0]
            for field, value in changes.items():
                if value is None:
                    conn.execute("DELETE FROM metadata_overrides WHERE document_id=? AND field=?", (document_id, field))
                else:
                    conn.execute("INSERT INTO metadata_overrides (document_id, field, value, subject, reason, set_at, "
                                 "catalogue_sequence) VALUES (?,?,?,?,?,?,?) ON CONFLICT (document_id, field) DO "
                                 "UPDATE SET value=excluded.value, subject=excluded.subject, reason=excluded.reason, "
                                 "set_at=excluded.set_at, catalogue_sequence=excluded.catalogue_sequence",
                                 (document_id, field, json.dumps(value), subject, reason.strip(), now, seq))
            after = self._effective(conn, document_id, revision_meta)
            conn.execute("UPDATE documents SET title=?, description=?, tags=?, business_area=?, owner=?, "
                         "updated_at=?, etag=? WHERE document_id=?",
                         (after["title"], after["description"], json.dumps(after["tags"]), after["business_area"],
                          after["owner"], now, etag, document_id))
            conn.execute("INSERT INTO metadata_audit (catalogue_sequence, document_id, subject, reason, changes, "
                         "before, after, occurred_at) VALUES (?,?,?,?,?,?,?,?)",
                         (seq, document_id, subject, reason.strip(), json.dumps(changes), json.dumps(before),
                          json.dumps(after), now))
        return self.get_metadata(document_id)

    def metadata_history(self, document_id) -> list[dict]:
        self.get_document(document_id)
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM metadata_audit WHERE document_id=? ORDER BY audit_id",
                                (document_id,)).fetchall()
        return [{"catalogue_sequence": r["catalogue_sequence"], "subject": r["subject"], "reason": r["reason"],
                 "changes": json.loads(r["changes"]), "before": json.loads(r["before"]),
                 "after": json.loads(r["after"]), "occurred_at": r["occurred_at"]} for r in rows]

    # ---- backup -----------------------------------------------------------------

    def backup(self, destination) -> dict:
        """Consistent catalogue copy (SQLite online backup) plus every referenced artifact."""
        dest = Path(destination)
        (dest / "artifacts").mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            target = sqlite3.connect(dest / "catalogue.sqlite3")
            try:
                conn.backup(target)
            finally:
                target.close()
            rows = conn.execute("SELECT artifact_key, stored_artifact_sha256 FROM revisions "
                                "WHERE status='committed'").fetchall()
        for key, digest in rows:
            src, out = self.root / key, dest / key
            out.parent.mkdir(parents=True, exist_ok=True)
            data = src.read_bytes()
            if _sha256(data) != digest:
                raise LibraryError("ARTIFACT_CORRUPT", f"{key} failed its integrity check", 500)
            out.write_bytes(data)
        return {"catalogue_sequence": self.catalogue_sequence(), "artifacts": len(rows)}
