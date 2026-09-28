"""Re-project an incoming artifact for the shared library (handoff 17.1 and 17.3).

The importer never trusts a producer's projection labels. It takes the native payload,
applies shared-projection/1 again, re-derives objects/sections/navigation, re-renders
with the trusted engine renderer, and builds a new canonical artifact that keeps the
document's identity, revision ID and metadata. Query code can only be withheld on
import, never restored: an artifact that arrives with code withheld stays withheld.
"""
from __future__ import annotations

import copy

from .build import ADAPTERS
from .envelope import assemble, fit_sections
from .projection import POLICY_VERSION, project

SUPPORTED_NATIVE_SCHEMAS = {"power_bi": {"pbi-doc-gen/2"}, "adf": {"adf-doc-gen/2"}}


class UnsupportedProjection(ValueError):
    """422 UNSUPPORTED_SAFE_PROJECTION: no safe projection exists for this native schema."""


def reproject(manifest: dict, *, query_code: str = "withheld"):
    """Return (stored artifact bytes, stored manifest) for a validated submitted manifest."""
    document_type = manifest["document_type"]
    adapter = ADAPTERS[document_type]
    native = manifest["projection"]["native_schema"]
    if native not in SUPPORTED_NATIVE_SCHEMAS[document_type]:
        raise UnsupportedProjection(f"no safe projection for native schema {native!r}")
    submitted = manifest["projection"]
    if submitted["profile"] == "shared" and submitted["options"]["query_code"] == "withheld":
        query_code = "withheld"                    # withheld code cannot come back
    data, omissions = project(document_type, manifest["native_payload"]["data"], query_code=query_code)
    if submitted["profile"] == "shared":           # keep what earlier projection already removed
        seen = {(o["path"], o["reason"]) for o in omissions}
        omissions += [o for o in submitted["omissions"] if (o["path"], o["reason"]) not in seen]
        omissions.sort(key=lambda o: (o["path"], o["reason"]))
    descriptor = manifest["publication"]["scope_descriptor"]
    coverage = "selection" if descriptor.get("kind") == "selection" else "complete"
    data = dict(data, title=manifest["title"])
    objects, sections, targets = adapter.describe(data, coverage)
    sections, trimmed = fit_sections(sections)
    stored = copy.deepcopy(manifest)
    stored.update(
        native_payload=dict(manifest["native_payload"], data=data), objects=objects, sections=sections,
        navigation={"targets": targets}, identity_version=adapter.IDENTITY_VERSION, content_sha256="0" * 64,
        projection={"policy_version": POLICY_VERSION, "native_schema": native, "profile": "shared",
                    "options": {"query_code": query_code}, "omissions": omissions,
                    "coverage_warnings": submitted["coverage_warnings"] + ([trimmed] if trimmed else [])})
    return assemble(adapter.render(data), stored, adapter.VIEW_IDS), stored
