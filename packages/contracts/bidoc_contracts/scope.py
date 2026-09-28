"""Canonical publication scopes (handoff section 17.2).

A stream is asset_id / document_type / environment_key / scope_key. The scope key is
derived from the engine-specific scope descriptor, so two different declared scopes
can never share a stream and the key cannot be edited independently of the scope.
"""
from __future__ import annotations

import hashlib
import json

POWER_BI_SCOPES = {"model_and_report", "model", "report"}
ADF_RESOURCE_TYPES = ("pipeline", "dataset", "linkedService", "dataflow", "trigger")


class ScopeError(ValueError):
    pass


def power_bi_scope(mode: str) -> dict:
    """pbi-doc-gen payload `mode` (combined / semantic-only / report-only) -> descriptor."""
    kind = {"combined": "model_and_report", "semantic-only": "model", "report-only": "report"}.get(mode)
    if kind is None:
        raise ScopeError(f"unknown Power BI mode {mode!r}")
    return {"kind": kind}


def adf_scope(whole_factory: bool, resources=()) -> dict:
    """A whole factory (Git folder / ARM export) or a declared selection of resources."""
    if whole_factory:
        return {"kind": "factory"}
    items = sorted(set(resources))
    for r in items:
        kind, _, name = r.partition("/")
        if kind not in ADF_RESOURCE_TYPES or not name:
            raise ScopeError(f"invalid ADF resource reference {r!r}; expected '<type>/<name>'")
    if not items:
        raise ScopeError("an ADF selection must name at least one resource")
    return {"kind": "selection", "resources": items}


def scope_key(document_type: str, descriptor: dict) -> str:
    kind = descriptor.get("kind") if isinstance(descriptor, dict) else None
    if document_type == "power_bi":
        if kind not in POWER_BI_SCOPES or set(descriptor) != {"kind"}:
            raise ScopeError("Power BI scope must be {'kind': model_and_report|model|report}")
        return kind
    if document_type == "adf":
        if kind == "factory" and set(descriptor) == {"kind"}:
            return "factory"
        if kind == "selection" and set(descriptor) == {"kind", "resources"}:
            canonical = adf_scope(False, descriptor["resources"])
            if canonical != descriptor:
                raise ScopeError("ADF selection resources must be sorted and distinct")
            digest = hashlib.sha256(json.dumps(canonical["resources"], separators=(",", ":")).encode()).hexdigest()
            return f"selection-{digest[:16]}"
        raise ScopeError("ADF scope must be {'kind': 'factory'} or {'kind': 'selection', 'resources': [...]}")
    raise ScopeError(f"unknown document type {document_type!r}")
