"""Build one envelope-v1 artifact from an engine payload."""
from __future__ import annotations

import uuid

from bidoc_contracts import scope_key

from . import adf, power_bi
from .envelope import assemble, fit_sections, now_rfc3339
from .projection import POLICY_VERSION, project

ADAPTERS = {"power_bi": power_bi, "adf": adf}


class NotPublishable(ValueError):
    """The snapshot is not complete for its declared scope; only local output is possible."""


def build_artifact(document_type: str, payload: dict, *, profile: str, query_code: str, identity,
                   environment_key: str, classification: dict, title: str, description: str = "",
                   tags=(), source_kind: str, source_label: str, source_sha256: str | None = None,
                   generator_version: str):
    adapter = ADAPTERS[document_type]
    descriptor, complete, warnings = adapter.scope(payload)
    if not complete or descriptor is None:
        raise NotPublishable("the input was not read completely, so this is not a complete snapshot of its scope")
    if profile == "shared":
        data, omissions = project(document_type, payload, query_code=query_code)
    elif profile == "local":
        data, omissions, query_code = payload, [], "included"
    else:
        raise ValueError("profile must be 'local' or 'shared'")
    title = title.strip()[:200] or adapter.ENGINE
    data = dict(data, title=title)          # one title in the view, the payload and the manifest
    coverage = "selection" if descriptor.get("kind") == "selection" else "complete"
    objects, sections, targets = adapter.describe(data, coverage)
    sections, trimmed = fit_sections(sections)
    if trimmed:
        warnings = list(warnings) + [trimmed]
    manifest = {
        "schema_version": 1, "document_type": document_type,
        "classification": {"business_area": classification.get("business_area", "")[:200],
                           "environment": classification.get("environment", "")[:200],
                           "owner": classification.get("owner", "")[:200]},
        "native_payload": {"schema_version": payload.get("schemaVersion"), "data": data,
                           "generator": f"{adapter.ENGINE} {adapter.ENGINE_VERSION}"},
        "objects": objects, "document_id": identity.document_id, "revision_id": str(uuid.uuid4()),
        "title": title, "description": description[:2000],
        "tags": sorted({t.strip()[:50] for t in tags if t.strip()})[:20],
        "generated_at": now_rfc3339(),
        "generator": {"name": "bi-doc-generator", "version": generator_version},
        "source": {"kind": source_kind, "label": source_label[:255] or source_kind,
                   **({"sha256": source_sha256} if source_sha256 else {})},
        "publication": {"asset_id": identity.asset_id, "environment_key": environment_key,
                        "scope_key": scope_key(document_type, descriptor), "scope_descriptor": descriptor,
                        "snapshot_state": "complete"},
        "projection": {"policy_version": POLICY_VERSION, "native_schema": adapter.native_schema(payload),
                       "profile": profile, "options": {"query_code": query_code}, "omissions": omissions,
                       "coverage_warnings": [w[:1000] for w in warnings][:1000]},
        "navigation": {"targets": targets}, "identity_version": adapter.IDENTITY_VERSION,
        "content_sha256": "0" * 64, "sections": sections,
    }
    html = adapter.render(data)
    return assemble(html, manifest, adapter.VIEW_IDS), manifest
