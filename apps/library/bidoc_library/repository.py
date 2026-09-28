"""What derived state and manual links need from a store (B10b).

Derived search and relationships (derived.py) and manual assertions (manual.py) use only
these methods, so they run unchanged over LocalStore (SQLite) and AzureStore (Table and
Blob). Every write is optimistic: it names the catalogue sequence it read, and it is
refused with StaleSequence if the catalogue moved in between. The caller re-reads and
retries, so a decision is never committed on stale inputs.

    read_sequence() -> int
    catalogue_documents() -> list[dict]      # every document, archived included
    is_committed(document_id, revision_id) -> bool

    derived_state() -> {"search": row | None, "relationships": row | None}
        row: {generation_sequence, state, snapshot_key, updated_at}
    put_derived_blob(key, data) / get_derived_blob(key)
    save_generation(generation, now)          # immutable record, before the switch
    switch_derived(seq, search_key, generation, now) -> bool   # only if seq is still current
    mark_derived_failed(seq)
    get_generation(generation_id) -> dict | None
    latest_generation_for(revision_id) -> dict | None
        {generation_id, catalogue_sequence, rule_version, completed_at,
         members: {document_id: revision_id}, detected: [...], manual: [...]}

    manual_active() -> list[record]           # status != deleted
    manual_get(relationship_id) -> record | None (deleted excluded)
    manual_write(observed_seq, record, expected_etag, action, subject, before)
        # create (expected_etag None) or replace; advances the sequence; audits
    manual_audit(relationship_id) -> list[{audit_id, relationship_id, action, subject,
                                           occurred_at, before, after}]  (before/after JSON text)
"""
from __future__ import annotations

import json
from pathlib import Path

from .errors import conflict

MANUAL_FIELDS = ("relationship_id", "source_document_id", "source_revision_id", "source_object_id",
                 "target_document_id", "target_revision_id", "target_object_id", "kind", "reason",
                 "creator_subject", "created_at", "updated_at", "etag", "status", "version")


class StaleSequence(Exception):
    """The catalogue changed after the caller read it; re-read and retry."""


class LocalRepository:
    """The repository methods over LocalStore's SQLite catalogue and data folder."""

    def read_sequence(self) -> int:
        return self.catalogue_sequence()

    def catalogue_documents(self) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM documents ORDER BY document_id").fetchall()
        return [{"document_id": r["document_id"], "document_type": r["document_type"],
                 "current_revision_id": r["current_revision_id"], "archived": bool(r["archived"]),
                 "title": r["title"], "tags": json.loads(r["tags"]), "business_area": r["business_area"],
                 "environment": r["environment"], "owner": r["owner"], "environment_key": r["environment_key"]}
                for r in rows]

    def is_committed(self, document_id, revision_id) -> bool:
        with self._db() as conn:
            return conn.execute("SELECT 1 FROM revisions WHERE document_id=? AND revision_id=? AND status='committed'",
                                (document_id, revision_id)).fetchone() is not None

    # ---- derived ----------------------------------------------------------------

    def derived_state(self) -> dict:
        with self._db() as conn:
            rows = {r["name"]: {"generation_sequence": r["generation_sequence"], "state": r["state"],
                                "snapshot_key": r["snapshot_key"], "updated_at": r["updated_at"]}
                    for r in conn.execute("SELECT * FROM derived_state")}
        return {"search": rows.get("search"), "relationships": rows.get("relationships")}

    def put_derived_blob(self, key: str, data: bytes) -> None:
        path = Path(self.root) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get_derived_blob(self, key: str) -> bytes:
        return (Path(self.root) / key).read_bytes()

    def save_generation(self, generation: dict, now: str) -> None:
        pass                                    # SQLite writes it with the switch, in one transaction

    def switch_derived(self, seq, search_key, g, now) -> bool:
        with self._db() as conn, self._tx(conn):
            if conn.execute("SELECT sequence FROM catalogue_state").fetchone()[0] != seq:
                return False
            conn.execute("INSERT INTO relationship_generations (generation_id, catalogue_sequence, "
                         "input_vector_sha256, rule_version, state, created_at, completed_at) "
                         "VALUES (?,?,?,?, 'ready', ?, ?)",
                         (g["generation_id"], seq, g["vector_sha256"], g["rule_version"], now, now))
            conn.executemany("INSERT INTO generation_members VALUES (?,?,?)",
                             [(g["generation_id"], d, r) for d, r in g["vector"]])
            conn.executemany(
                "INSERT INTO detected_relationships (relationship_id, generation_id, source_document_id, "
                "source_revision_id, source_object_id, target_document_id, target_revision_id, target_object_id, "
                "kind, confidence, evidence, rule_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(r["relationship_id"], g["generation_id"], r["source_document_id"], r["source_revision_id"],
                  r["source_object_id"], r["target_document_id"], r["target_revision_id"], r["target_object_id"],
                  r["kind"], r["confidence"], json.dumps(r["evidence"]), r["rule_version"]) for r in g["detected"]])
            conn.executemany("INSERT INTO generation_manual VALUES (?,?,?,?)",
                             [(g["generation_id"], m["relationship_id"], m["version"], json.dumps(m))
                              for m in g["manual"]])
            for name, key in (("search", search_key), ("relationships", g["generation_id"])):
                conn.execute("INSERT INTO derived_state (name, generation_sequence, state, updated_at, snapshot_key) "
                             "VALUES (?, ?, 'ready', ?, ?) ON CONFLICT(name) DO UPDATE SET "
                             "generation_sequence=excluded.generation_sequence, state='ready', "
                             "updated_at=excluded.updated_at, snapshot_key=excluded.snapshot_key",
                             (name, seq, now, key))
        return True

    def mark_derived_failed(self, seq) -> None:
        with self._db() as conn, self._tx(conn):
            for name in ("search", "relationships"):
                conn.execute("INSERT INTO derived_state (name, generation_sequence, state, updated_at) "
                             "VALUES (?, ?, 'failed', ?) ON CONFLICT(name) DO UPDATE SET state='failed', "
                             "updated_at=excluded.updated_at", (name, seq, self.now()))

    def _generation(self, conn, g) -> dict | None:
        if g is None:
            return None
        gid = g["generation_id"]
        detected = []
        for r in conn.execute("SELECT * FROM detected_relationships WHERE generation_id=?", (gid,)):
            detected.append({k: r[k] for k in ("relationship_id", "source_document_id", "source_revision_id",
                                               "source_object_id", "target_document_id", "target_revision_id",
                                               "target_object_id", "kind", "confidence", "rule_version")}
                            | {"evidence": json.loads(r["evidence"])})
        return {"generation_id": gid, "catalogue_sequence": g["catalogue_sequence"], "rule_version": g["rule_version"],
                "completed_at": g["completed_at"],
                "members": {r["document_id"]: r["revision_id"] for r in conn.execute(
                    "SELECT document_id, revision_id FROM generation_members WHERE generation_id=?", (gid,))},
                "detected": detected,
                "manual": [json.loads(r["record"]) for r in conn.execute(
                    "SELECT record FROM generation_manual WHERE generation_id=?", (gid,))]}

    def get_generation(self, generation_id) -> dict | None:
        with self._db() as conn:
            g = conn.execute("SELECT * FROM relationship_generations WHERE generation_id=? AND state='ready'",
                             (generation_id,)).fetchone()
            return self._generation(conn, g)

    def latest_generation_for(self, revision_id) -> dict | None:
        with self._db() as conn:
            g = conn.execute(
                "SELECT g.* FROM relationship_generations g JOIN generation_members m "
                "ON m.generation_id = g.generation_id WHERE m.revision_id=? AND g.state='ready' "
                "ORDER BY g.catalogue_sequence DESC LIMIT 1", (revision_id,)).fetchone()
            return self._generation(conn, g)

    # ---- manual links -------------------------------------------------------------

    def manual_active(self) -> list[dict]:
        with self._db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM manual_relationships WHERE status != 'deleted'")]

    def manual_get(self, relationship_id) -> dict | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM manual_relationships WHERE relationship_id=? AND status != 'deleted'",
                               (relationship_id,)).fetchone()
        return dict(row) if row else None

    def manual_write(self, observed_seq, record, expected_etag, action, subject, before) -> None:
        with self._db() as conn, self._tx(conn):
            if conn.execute("SELECT sequence FROM catalogue_state").fetchone()[0] != observed_seq:
                raise StaleSequence()
            row = conn.execute("SELECT etag, status FROM manual_relationships WHERE relationship_id=?",
                               (record["relationship_id"],)).fetchone()
            if expected_etag is not None and (row is None or row["status"] == "deleted" or row["etag"] != expected_etag):
                raise conflict("REVISION_CONFLICT", "the link changed since it was read",
                               current_etag=row["etag"] if row else None)
            conn.execute(f"INSERT OR REPLACE INTO manual_relationships ({', '.join(MANUAL_FIELDS)}) "
                         f"VALUES ({', '.join('?' * len(MANUAL_FIELDS))})", [record[f] for f in MANUAL_FIELDS])
            conn.execute("INSERT INTO relationship_audit (relationship_id, action, subject, occurred_at, before, after) "
                         "VALUES (?,?,?,?,?,?)",
                         (record["relationship_id"], action, subject, self.now(),
                          json.dumps(before) if before else None,
                          json.dumps(record) if action != "delete" else None))
            conn.execute("UPDATE catalogue_state SET sequence = sequence + 1 WHERE id=1")

    def manual_audit(self, relationship_id) -> list[dict]:
        with self._db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM relationship_audit WHERE relationship_id=? "
                                                  "ORDER BY audit_id", (relationship_id,))]
