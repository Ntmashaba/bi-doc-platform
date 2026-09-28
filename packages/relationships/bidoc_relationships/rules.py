"""rel-rules/1: match ADF activity bindings to Power BI source bindings (handoff 16, 17.2).

Pure and deterministic, so the hosted library and offline exports compute the same
result. Direction is stored once: source = ADF activity, target = Power BI source.

    produces   ADF writes the endpoint the Power BI source reads
    deletes    ADF deletes it (never a producer)
    reads      ADF also reads it (a related reader, never a producer)

Confidence:
    exact_static  every identity part present on both sides and equal as written
                  (SQL: server, port, instance, database, schema, object; files:
                  storage account, container, path), both resolved statically,
                  and no known environment contradiction
    possible      names match but identity is incomplete, differs only in letter
                  case, is a folder that contains the file, or is dynamic/opaque

Contradictions reject a match outright: different known servers, ports, instances,
databases, storage accounts or containers, or different known environments.
No match means "No relationship found in the indexed documents", never that no
upstream system exists.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

RULE_VERSION = "rel-rules/1"
KINDS = {"write": "produces", "delete": "deletes", "read": "reads"}


@dataclass(frozen=True)
class Document:
    document_id: str
    revision_id: str
    document_type: str            # power_bi | adf
    environment_key: str          # "unknown" when not declared
    objects: tuple                # envelope object descriptors (with bindings)


def _lower(v):
    return v.lower() if isinstance(v, str) else v


def _file_parts(ep: dict):
    """(account, container, path) with container/path derived from a URL when absent."""
    account, container, path = ep.get("storage_account"), ep.get("container"), ep.get("path")
    url = ep.get("url")
    if url and (container is None or path is None):
        segments = [s for s in unquote(urlparse(url).path).split("/") if s]
        if container is None and segments:
            container, segments = segments[0], segments[1:]
        if path is None and segments:
            path = "/".join(segments)
    return _lower(account), _lower(container), (path or "").strip("/") or None


def _compare_sql(a: dict, b: dict):
    """(verdict, notes): verdict is 'exact', 'possible' or None (no match)."""
    if not a.get("object") or not b.get("object") or _lower(a["object"]) != _lower(b["object"]):
        return None, []
    notes, complete, case_exact = [], True, a["object"] == b["object"]
    if _lower(a.get("instance")) != _lower(b.get("instance")):
        return None, []                        # a named instance and the default instance differ
    for field in ("server", "port"):
        x, y = _lower(a.get(field)), _lower(b.get(field))
        if x is not None and y is not None and x != y:
            return None, []
        if (x is None) != (y is None):
            complete = False
            notes.append(f"{field} known on one side only")
    for field in ("database", "schema"):
        x, y = a.get(field), b.get(field)
        if x is not None and y is not None:
            if _lower(x) != _lower(y):
                return None, []
            case_exact &= x == y
        else:
            complete = False
            notes.append(f"{field} not known on both sides")
    if not a.get("server") or not b.get("server"):
        complete = False
    if not case_exact:
        notes.append("names differ only in letter case; the collation is unknown")
    return ("exact" if complete and case_exact else "possible"), notes


def _compare_file(a: dict, b: dict):
    acc_a, con_a, path_a = _file_parts(a)
    acc_b, con_b, path_b = _file_parts(b)
    if not acc_a or not acc_b or acc_a != acc_b:
        return None, []
    if con_a and con_b and con_a != con_b:
        return None, []
    if not path_a or not path_b:
        return None, []
    if path_a == path_b and con_a and con_b:
        return "exact", []
    if path_a == path_b:
        return "possible", ["container not known on both sides"]
    if path_b.startswith(path_a + "/"):
        return "possible", ["the pipeline writes the folder that contains this file"]
    if path_a.lower() == path_b.lower():
        return "possible", ["paths differ only in letter case"]
    return None, []


def _is_file(ep):
    return bool(ep.get("storage_account") or ep.get("path") or (ep.get("url") and not ep.get("server")))


def _relationship_id(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()[:32]


def detect(documents):
    """All detected relationships among the given current revisions, deterministically ordered."""
    adf = [d for d in documents if d.document_type == "adf"]
    pbi = [d for d in documents if d.document_type == "power_bi"]
    targets = [(d, o, b) for d in pbi for o in d.objects if o["kind"] == "source"
               for b in o["bindings"] if b["operation"] == "read"]
    found = {}
    for sd in adf:
        for so in sd.objects:
            for sb in so["bindings"]:
                kind = KINDS.get(sb["operation"])
                if kind is None:
                    continue
                for td, to, tb in targets:
                    env = {sd.environment_key, td.environment_key} - {"unknown"}
                    if len(env) > 1:
                        continue                                   # different known environments
                    a, b = sb["normalized_endpoint"], tb["normalized_endpoint"]
                    if _is_file(a) != _is_file(b):
                        continue
                    verdict, notes = (_compare_file if _is_file(a) else _compare_sql)(a, b)
                    if verdict is None:
                        continue
                    uncertain = [r for r in (sb["resolution"], tb["resolution"]) if r != "static"]
                    if uncertain:
                        verdict = "possible"
                        notes = notes + [f"resolution is {', '.join(sorted(set(uncertain)))}"]
                    if "unknown" in (sd.environment_key, td.environment_key):
                        notes = notes + ["environment not declared on both documents"]
                    coverage = sorted({sb["coverage"], tb["coverage"]} - {"complete"})
                    rid = _relationship_id(RULE_VERSION, sd.document_id, so["object_id"], sb["binding_id"],
                                           td.document_id, to["object_id"], tb["binding_id"], kind)
                    found[rid] = {
                        "relationship_id": rid, "kind": kind,
                        "confidence": "exact_static" if verdict == "exact" else "possible", "origin": "detected",
                        "source_document_id": sd.document_id, "source_revision_id": sd.revision_id,
                        "source_object_id": so["object_id"], "source_parent_object_id": so.get("parent_object_id"),
                        "target_document_id": td.document_id, "target_revision_id": td.revision_id,
                        "target_object_id": to["object_id"], "rule_version": RULE_VERSION,
                        "evidence": {"source_binding_id": sb["binding_id"], "target_binding_id": tb["binding_id"],
                                     "source_endpoint": sb["endpoint"], "target_endpoint": tb["endpoint"],
                                     "source_environment": sd.environment_key, "target_environment": td.environment_key,
                                     "source_resolution": sb["resolution"], "target_resolution": tb["resolution"],
                                     "coverage_warnings": [f"{c} coverage" for c in coverage], "notes": notes},
                    }
    return [found[k] for k in sorted(found)]
