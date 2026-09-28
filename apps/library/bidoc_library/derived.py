"""Derived state: search snapshots and relationship generations (handoff 16, 17.3, 17.4).

Derived state is rebuilt from committed rows only. A rebuild records the catalogue
sequence it read; its pointer is switched only if that sequence is still current, so a
stale computation can never overwrite a newer one. The pilot rebuilds synchronously
after each committed change and on reads that find it behind; nothing depends on an
untracked background task.

Relationship generations pin the exact revision vector, the detected relationships
and the manual assertions (with versions) as they stood, so historical links are
reproducible after later revisions and edits.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from functools import lru_cache
from pathlib import Path

from bidoc_contracts import locate_manifest
from bidoc_relationships import RULE_VERSION, Document, detect

from .errors import LibraryError, not_found


class Derived:
    def __init__(self, store):
        self.store = store
        self.dir = Path(store.root) / "derived"
        self.dir.mkdir(exist_ok=True)
        self._manifest = lru_cache(maxsize=512)(self._load_manifest)

    # ---- inputs -----------------------------------------------------------------

    def _load_manifest(self, document_id: str, revision_id: str) -> dict:
        data = self.store.read_artifact(document_id, revision_id)       # integrity-checked
        return json.loads(locate_manifest(data).body)

    def manifest(self, document_id, revision_id) -> dict:
        return self._manifest(document_id, revision_id)

    def _current(self, conn):
        rows = conn.execute("SELECT * FROM documents WHERE archived=0 ORDER BY document_id").fetchall()
        return [(r, self.manifest(r["document_id"], r["current_revision_id"])) for r in rows]

    # ---- rebuild ----------------------------------------------------------------

    def status(self) -> dict:
        with self.store._db() as conn:
            seq = conn.execute("SELECT sequence FROM catalogue_state").fetchone()[0]
            rows = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM derived_state")}
        return {"catalogue_sequence": seq, "search": rows.get("search"), "relationships": rows.get("relationships")}

    def refresh(self) -> dict:
        """Bring search and relationships up to the current sequence; returns the state."""
        with self.store._db() as conn:
            seq = conn.execute("SELECT sequence FROM catalogue_state").fetchone()[0]
            have = {r["name"]: r["generation_sequence"] for r in
                    conn.execute("SELECT name, generation_sequence FROM derived_state WHERE state='ready'")}
            if have.get("search") == seq and have.get("relationships") == seq:
                return {"state": "ready", "catalogue_sequence": seq}
            current = self._current(conn)
            manual = [dict(r) for r in conn.execute("SELECT * FROM manual_relationships WHERE status != 'deleted'")]
        try:
            snapshot = self._build_search(current, seq)
            generation = self._build_relationships(current, manual, seq)
        except Exception:
            self._mark_failed(seq)
            raise
        now = self.store.now()
        with self.store._db() as conn, self.store._tx(conn):
            if conn.execute("SELECT sequence FROM catalogue_state").fetchone()[0] != seq:
                return {"state": "stale", "catalogue_sequence": seq}      # a newer change will rebuild
            conn.execute("INSERT INTO derived_state (name, generation_sequence, state, updated_at, snapshot_key) "
                         "VALUES ('search', ?, 'ready', ?, ?) ON CONFLICT(name) DO UPDATE SET "
                         "generation_sequence=excluded.generation_sequence, state='ready', "
                         "updated_at=excluded.updated_at, snapshot_key=excluded.snapshot_key", (seq, now, snapshot))
            self._commit_generation(conn, generation, seq, now)
            conn.execute("INSERT INTO derived_state (name, generation_sequence, state, updated_at, snapshot_key) "
                         "VALUES ('relationships', ?, 'ready', ?, ?) ON CONFLICT(name) DO UPDATE SET "
                         "generation_sequence=excluded.generation_sequence, state='ready', "
                         "updated_at=excluded.updated_at, snapshot_key=excluded.snapshot_key",
                         (seq, now, generation["generation_id"]))
        return {"state": "ready", "catalogue_sequence": seq}

    def _mark_failed(self, seq):
        with self.store._db() as conn, self.store._tx(conn):
            for name in ("search", "relationships"):
                conn.execute("INSERT INTO derived_state (name, generation_sequence, state, updated_at) "
                             "VALUES (?, ?, 'failed', ?) ON CONFLICT(name) DO UPDATE SET state='failed', "
                             "updated_at=excluded.updated_at", (name, seq, self.store.now()))

    def _build_search(self, current, seq) -> str:
        documents = [{"document_id": r["document_id"], "revision_id": r["current_revision_id"],
                      "document_type": r["document_type"],
                      "classification": {"business_area": r["business_area"], "environment": r["environment"],
                                         "owner": r["owner"]},
                      "title": r["title"], "tags": json.loads(r["tags"]),
                      "sections": [{"id": s["id"], "title": s["title"], "text": s["text"]} for s in m["sections"]]}
                     for r, m in current]
        key = f"derived/search-{seq}.json"
        path = Path(self.store.root) / key
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"generation": seq, "documents": documents}, ensure_ascii=False,
                                  separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        return key

    def _build_relationships(self, current, manual, seq) -> dict:
        docs = [Document(r["document_id"], r["current_revision_id"], r["document_type"], r["environment_key"],
                         tuple(m["objects"])) for r, m in current]
        vector = sorted((d.document_id, d.revision_id) for d in docs)
        return {"generation_id": str(uuid.uuid4()), "vector": vector,
                "vector_sha256": hashlib.sha256(json.dumps([vector, RULE_VERSION]).encode()).hexdigest(),
                "detected": detect(docs), "manual": manual}

    def _commit_generation(self, conn, g, seq, now):
        conn.execute("INSERT INTO relationship_generations (generation_id, catalogue_sequence, input_vector_sha256, "
                     "rule_version, state, created_at, completed_at) VALUES (?,?,?,?, 'ready', ?, ?)",
                     (g["generation_id"], seq, g["vector_sha256"], RULE_VERSION, now, now))
        conn.executemany("INSERT INTO generation_members VALUES (?,?,?)",
                         [(g["generation_id"], d, r) for d, r in g["vector"]])
        conn.executemany(
            "INSERT INTO detected_relationships (relationship_id, generation_id, source_document_id, "
            "source_revision_id, source_object_id, target_document_id, target_revision_id, target_object_id, "
            "kind, confidence, evidence, rule_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["relationship_id"], g["generation_id"], r["source_document_id"], r["source_revision_id"],
              r["source_object_id"], r["target_document_id"], r["target_revision_id"], r["target_object_id"],
              r["kind"], r["confidence"],
              json.dumps(dict(r["evidence"], source_parent_object_id=r["source_parent_object_id"])),
              r["rule_version"]) for r in g["detected"]])
        conn.executemany("INSERT INTO generation_manual VALUES (?,?,?,?)",
                         [(g["generation_id"], m["relationship_id"], m["version"], json.dumps(m)) for m in g["manual"]])

    # ---- reads ------------------------------------------------------------------

    def search_index(self) -> dict:
        """The newest ready snapshot, refreshed first; labelled stale if it could not be."""
        state = "ready"
        try:
            if self.refresh()["state"] != "ready":
                state = "updating"
        except Exception:  # noqa: BLE001 - serve the prior snapshot, labelled
            state = "stale"
        with self.store._db() as conn:
            row = conn.execute("SELECT * FROM derived_state WHERE name='search' AND snapshot_key IS NOT NULL").fetchone()
            seq = conn.execute("SELECT sequence FROM catalogue_state").fetchone()[0]
        if row is None:
            return {"generation": 0, "state": "ready" if seq == 0 else state, "documents": []}
        data = json.loads((Path(self.store.root) / row["snapshot_key"]).read_text(encoding="utf-8"))
        if row["generation_sequence"] != seq and state == "ready":
            state = "updating"
        return {"generation": data["generation"], "state": state, "documents": data["documents"]}

    def objects(self, document_id, revision_id) -> list:
        m = self.manifest(document_id, revision_id)
        targets = {t["target_id"]: t for t in m["navigation"]["targets"]}
        return [{"object_id": o["object_id"], "kind": o["kind"], "label": o["label"], "section_id": o["section_id"],
                 "parent_object_id": o["parent_object_id"], "dynamic": o["dynamic"], "opaque": o["opaque"],
                 "coverage": o["coverage"], "view": targets.get(o["object_id"])} for o in m["objects"]]

    def section_targets(self, document_id, revision_id) -> list:
        """Navigation targets that address a section rather than an object (e.g. overview, pages)."""
        m = self.manifest(document_id, revision_id)
        sections = {s["id"] for s in m["sections"]}
        return [{"section_id": t["target_id"], "view": t} for t in m["navigation"]["targets"]
                if t["target_id"] in sections]

    def _generation_for(self, conn, revision_id, generation_id):
        if generation_id:
            g = conn.execute("SELECT * FROM relationship_generations WHERE generation_id=? AND state='ready'",
                             (generation_id,)).fetchone()
            if g is None or not conn.execute("SELECT 1 FROM generation_members WHERE generation_id=? AND revision_id=?",
                                             (generation_id, revision_id)).fetchone():
                raise LibraryError("EVIDENCE_UNAVAILABLE", "that generation does not contain this revision", 404)
            return g
        return conn.execute(
            "SELECT g.* FROM relationship_generations g JOIN generation_members m ON m.generation_id = g.generation_id "
            "WHERE m.revision_id=? AND g.state='ready' ORDER BY g.catalogue_sequence DESC LIMIT 1",
            (revision_id,)).fetchone()

    def relationships(self, document_id, *, revision_id=None, generation_id=None, object_id=None,
                      can_see_archived=False) -> dict:
        try:
            self.refresh()
        except Exception:  # noqa: BLE001 - fall back to the newest ready generation, labelled below
            pass
        with self.store._db() as conn:
            doc = conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
            if doc is None or (doc["archived"] and not can_see_archived):
                raise not_found("document")
            revision_id = revision_id or doc["current_revision_id"]
            if not conn.execute("SELECT 1 FROM revisions WHERE document_id=? AND revision_id=? AND status='committed'",
                                (document_id, revision_id)).fetchone():
                raise not_found("revision")
            g = self._generation_for(conn, revision_id, generation_id)
            seq = conn.execute("SELECT sequence FROM catalogue_state").fetchone()[0]
            if g is None:
                return {"document_id": document_id, "revision_id": revision_id,
                        "generation": {"state": "evidence_unavailable"}, "incoming": [], "outgoing": [], "manual": []}
            rows = conn.execute("SELECT * FROM detected_relationships WHERE generation_id=? AND "
                                "(source_document_id=? OR target_document_id=?)",
                                (g["generation_id"], document_id, document_id)).fetchall()
            manual = [json.loads(r["record"]) for r in conn.execute(
                "SELECT record FROM generation_manual WHERE generation_id=?", (g["generation_id"],))]
            members = {r["document_id"]: r["revision_id"] for r in conn.execute(
                "SELECT document_id, revision_id FROM generation_members WHERE generation_id=?", (g["generation_id"],))}
            docs = {r["document_id"]: r for r in conn.execute("SELECT * FROM documents")}
        historical = revision_id != doc["current_revision_id"] or generation_id is not None
        state = "ready" if g["catalogue_sequence"] == seq else ("pinned" if historical else "updating")
        incoming, outgoing = [], []
        for r in rows:
            out = r["source_document_id"] == document_id
            this_obj = r["source_object_id"] if out else r["target_object_id"]
            evidence = json.loads(r["evidence"])
            if object_id and this_obj != object_id and evidence.get("source_parent_object_id") != object_id:
                continue
            other_doc = r["target_document_id"] if out else r["source_document_id"]
            other_rev = r["target_revision_id"] if out else r["source_revision_id"]
            other = docs.get(other_doc)
            if other is None or (other["archived"] and not can_see_archived):
                continue                                            # never leak inaccessible counterparts
            other_obj = r["target_object_id"] if out else r["source_object_id"]
            labels = {o["object_id"]: o["label"] for o in self.manifest(other_doc, other_rev)["objects"]}
            item = {"relationship_id": r["relationship_id"], "kind": r["kind"], "origin": "detected",
                    "confidence": r["confidence"], "rule_version": r["rule_version"], "object_id": this_obj,
                    "counterpart": {"document_id": other_doc, "revision_id": other_rev, "object_id": other_obj,
                                    "object_label": labels.get(other_obj, other_obj), "title": other["title"],
                                    "document_type": other["document_type"], "environment": other["environment"],
                                    "archived": bool(other["archived"]),
                                    "parent_object_id": evidence.get("source_parent_object_id") if not out else None},
                    "evidence": evidence}
            (outgoing if out else incoming).append(item)
        manual_items = []
        for m in manual:
            if document_id not in (m["source_document_id"], m["target_document_id"]):
                continue
            out = m["source_document_id"] == document_id
            this_obj = m["source_object_id"] if out else m["target_object_id"]
            if object_id and this_obj not in (None, object_id):
                continue
            other_doc = m["target_document_id"] if out else m["source_document_id"]
            other = docs.get(other_doc)
            if other is None or (other["archived"] and not can_see_archived):
                continue
            other_obj = m["target_object_id"] if out else m["source_object_id"]

            def present(doc_id, obj_id):
                rev = members.get(doc_id)
                return rev is not None and (obj_id is None or obj_id in
                                            {o["object_id"] for o in self.manifest(doc_id, rev)["objects"]})
            # Both ends must still exist; a missing object needs review, never fuzzy reassignment.
            both = present(m["source_document_id"], m["source_object_id"]) and \
                present(m["target_document_id"], m["target_object_id"])
            status = "archived_target" if other["archived"] else "active" if both else "needs_review"
            manual_items.append({"relationship_id": m["relationship_id"], "kind": m["kind"], "origin": "manual",
                                 "confidence": "user_asserted", "direction": "outgoing" if out else "incoming",
                                 "object_id": this_obj, "reason": m["reason"], "version": m["version"],
                                 "creator_subject": m["creator_subject"], "created_at": m["created_at"],
                                 "updated_at": m["updated_at"], "status": status,
                                 "counterpart": {"document_id": other_doc, "object_id": other_obj,
                                                 "title": other["title"], "document_type": other["document_type"],
                                                 "revision_at_assertion": m["target_revision_id" if out else
                                                                            "source_revision_id"]}})
        key = lambda x: (x["counterpart"]["title"].lower(), x["relationship_id"])  # noqa: E731
        return {"document_id": document_id, "revision_id": revision_id,
                "generation": {"generation_id": g["generation_id"], "catalogue_sequence": g["catalogue_sequence"],
                               "current_catalogue_sequence": seq, "state": state, "rule_version": g["rule_version"],
                               "computed_at": g["completed_at"],
                               "input_revisions": [{"document_id": d, "revision_id": r} for d, r in sorted(members.items())]},
                "incoming": sorted(incoming, key=key), "outgoing": sorted(outgoing, key=key),
                "manual": sorted(manual_items, key=key)}
