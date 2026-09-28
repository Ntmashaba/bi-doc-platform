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

from bidoc_contracts import locate_manifest
from bidoc_relationships import RULE_VERSION, Document, detect

from .errors import LibraryError, not_found


class Derived:
    def __init__(self, store):
        self.store = store                     # any repository (repository.py)
        self._manifest = lru_cache(maxsize=512)(self._load_manifest)

    # ---- inputs -----------------------------------------------------------------

    def _load_manifest(self, document_id: str, revision_id: str) -> dict:
        data = self.store.read_artifact(document_id, revision_id)       # integrity-checked
        return json.loads(locate_manifest(data).body)

    def manifest(self, document_id, revision_id) -> dict:
        return self._manifest(document_id, revision_id)

    def _current(self):
        docs = [d for d in self.store.catalogue_documents() if not d["archived"]]
        return [(d, self.manifest(d["document_id"], d["current_revision_id"])) for d in docs]

    # ---- rebuild ----------------------------------------------------------------

    def status(self) -> dict:
        state = self.store.derived_state()
        return {"catalogue_sequence": self.store.read_sequence(), "search": state["search"],
                "relationships": state["relationships"]}

    def refresh(self) -> dict:
        """Bring search and relationships up to the current sequence; returns the state."""
        seq = self.store.read_sequence()
        state = self.store.derived_state()
        if all(state[n] and state[n]["state"] == "ready" and state[n]["generation_sequence"] == seq
               for n in ("search", "relationships")):
            return {"state": "ready", "catalogue_sequence": seq}
        # Everything read below is at least as new as `seq`; the switch only happens if the
        # catalogue is still at `seq`, so nothing newer can be overwritten.
        current = self._current()
        manual = self.store.manual_active()
        now = self.store.now()
        try:
            search_key = self._build_search(current, seq)
            generation = self._build_relationships(current, manual, seq)
            self.store.save_generation(generation, now)
        except Exception:
            self.store.mark_derived_failed(seq)
            raise
        if not self.store.switch_derived(seq, search_key, generation, now):
            return {"state": "stale", "catalogue_sequence": seq}      # a newer change will rebuild
        return {"state": "ready", "catalogue_sequence": seq}

    def _build_search(self, current, seq) -> str:
        documents = [{"document_id": d["document_id"], "revision_id": d["current_revision_id"],
                      "document_type": d["document_type"],
                      "classification": {"business_area": d["business_area"], "environment": d["environment"],
                                         "owner": d["owner"]},
                      "title": d["title"], "tags": d["tags"],
                      "sections": [{"id": s["id"], "title": s["title"], "text": s["text"]} for s in m["sections"]]}
                     for d, m in current]
        key = f"derived/search-{seq}-{uuid.uuid4().hex[:8]}.json"
        self.store.put_derived_blob(key, json.dumps({"generation": seq, "documents": documents}, ensure_ascii=False,
                                                    separators=(",", ":")).encode("utf-8"))
        return key

    def _build_relationships(self, current, manual, seq) -> dict:
        docs = [Document(d["document_id"], d["current_revision_id"], d["document_type"], d["environment_key"],
                         tuple(m["objects"])) for d, m in current]
        vector = sorted((d.document_id, d.revision_id) for d in docs)
        detected = [{**{k: v for k, v in r.items() if k != "source_parent_object_id"},
                     "evidence": dict(r["evidence"], source_parent_object_id=r["source_parent_object_id"])}
                    for r in detect(docs)]
        return {"generation_id": str(uuid.uuid4()), "catalogue_sequence": seq, "vector": vector,
                "members": dict(vector), "rule_version": RULE_VERSION,
                "vector_sha256": hashlib.sha256(json.dumps([vector, RULE_VERSION]).encode()).hexdigest(),
                "detected": detected, "manual": manual}

    # ---- reads ------------------------------------------------------------------

    def search_index(self) -> dict:
        """The newest ready snapshot, refreshed first; labelled stale if it could not be."""
        state = "ready"
        try:
            if self.refresh()["state"] != "ready":
                state = "updating"
        except Exception:  # noqa: BLE001 - serve the prior snapshot, labelled
            state = "stale"
        row = self.store.derived_state()["search"]
        seq = self.store.read_sequence()
        if row is None or not row.get("snapshot_key"):
            return {"generation": 0, "state": "ready" if seq == 0 else state, "documents": []}
        data = json.loads(self.store.get_derived_blob(row["snapshot_key"]).decode("utf-8"))
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

    def _generation_for(self, revision_id, generation_id):
        if generation_id:
            g = self.store.get_generation(generation_id)
            if g is None or revision_id not in g["members"].values():
                raise LibraryError("EVIDENCE_UNAVAILABLE", "that generation does not contain this revision", 404)
            return g
        return self.store.latest_generation_for(revision_id)

    def relationships(self, document_id, *, revision_id=None, generation_id=None, object_id=None,
                      can_see_archived=False) -> dict:
        try:
            self.refresh()
        except Exception:  # noqa: BLE001 - fall back to the newest ready generation, labelled below
            pass
        docs = {d["document_id"]: d for d in self.store.catalogue_documents()}
        doc = docs.get(document_id)
        if doc is None or (doc["archived"] and not can_see_archived):
            raise not_found("document")
        revision_id = revision_id or doc["current_revision_id"]
        if not self.store.is_committed(document_id, revision_id):
            raise not_found("revision")
        g = self._generation_for(revision_id, generation_id)
        seq = self.store.read_sequence()
        if g is None:
            return {"document_id": document_id, "revision_id": revision_id,
                    "generation": {"state": "evidence_unavailable"}, "incoming": [], "outgoing": [], "manual": []}
        rows = [r for r in g["detected"] if document_id in (r["source_document_id"], r["target_document_id"])]
        manual = g["manual"]
        members = g["members"]
        historical = revision_id != doc["current_revision_id"] or generation_id is not None
        state = "ready" if g["catalogue_sequence"] == seq else ("pinned" if historical else "updating")
        incoming, outgoing = [], []
        for r in rows:
            out = r["source_document_id"] == document_id
            this_obj = r["source_object_id"] if out else r["target_object_id"]
            evidence = r["evidence"]
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
