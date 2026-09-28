"""What any library backend accepts, before storage is involved (handoff 4, 17.1 and 17.3).

Shared by LocalStore and AzureStore so both admit exactly the same artifacts: the
envelope is validated, known legacy HTML is converted, and every stored copy is
re-projected and re-rendered by the trusted engines.
"""
from __future__ import annotations

from bidoc_contracts import ContractError, is_zip, validate_artifact, validate_zip
from bidoc_engines import legacy
from bidoc_engines.convert import UnsupportedProjection, reproject

from .errors import LibraryError


def convert_legacy(data: bytes, metadata: dict, target, *, limits) -> dict:
    """Known engine HTML without a manifest: extract, project and re-render.

    `target` is the existing document (from get_document) for a new version of it, or None
    for a new stream identity.
    """
    stream = {}
    if target:
        stream = {"document_id": target["document_id"], "asset_id": target["publication"]["asset_id"],
                  "environment": target["classification"]["environment"]}
    try:
        artifact, _ = legacy.convert(data, metadata, **stream)
        manifest = validate_artifact(artifact, limits=limits)
    except UnsupportedProjection as exc:
        raise LibraryError("UNSUPPORTED_SAFE_PROJECTION", str(exc), 422) from None
    except ContractError as exc:
        raise LibraryError("CONTRACT_INVALID", f"conversion failed: {exc}", 422) from None
    manifest["projection"]["coverage_warnings"].append(legacy.DETAILS_NOT_KEPT)
    return manifest


ASSETS_NOT_KEPT = ("{n} asset file(s) from the ZIP were verified but not kept: the library re-renders documents "
                   "with its trusted engines, which do not use them.")


def _contract_error(exc: ContractError) -> LibraryError:
    return LibraryError(exc.code if exc.code in ("ARTIFACT_TOO_LARGE",) else "CONTRACT_INVALID",
                        str(exc), 413 if exc.code == "ARTIFACT_TOO_LARGE" else 422,
                        {"contract_code": exc.code, "issues": exc.issues[:20]})


def admit_zip(data: bytes, *, limits) -> dict:
    """The ZIP profile: every archive rule and asset hash is checked, then only the verified
    document.html goes on (its manifest without the asset list)."""
    try:
        z = validate_zip(data, limits=limits)
    except ContractError as exc:
        raise _contract_error(exc) from None
    manifest = z.manifest
    if manifest.pop("assets", None):
        manifest["projection"]["coverage_warnings"].append(ASSETS_NOT_KEPT.format(n=len(z.assets)))
    return manifest


def admit(data: bytes, *, limits, legacy_metadata, target_document_id, get_document) -> dict:
    """The validated manifest to publish (envelope, ZIP profile, or converted legacy HTML)."""
    if is_zip(data):
        return admit_zip(data, limits=limits)
    try:
        return validate_artifact(data, limits=limits)
    except ContractError as exc:
        if exc.code == "MANIFEST_MISSING":
            target = get_document(target_document_id) if target_document_id else None
            return convert_legacy(data, legacy_metadata or {}, target, limits=limits)
        raise _contract_error(exc) from None


def admit_for_preview(data: bytes, *, limits, legacy_metadata):
    """(input kind, manifest) without storing anything."""
    if is_zip(data):
        return "zip", admit_zip(data, limits=limits)
    try:
        return "envelope", validate_artifact(data, limits=limits)
    except ContractError as exc:
        if exc.code != "MANIFEST_MISSING":
            raise LibraryError("CONTRACT_INVALID", str(exc), 422, {"contract_code": exc.code}) from None
        try:
            metadata = legacy.parse_metadata(legacy_metadata)
        except ValueError as err:
            raise LibraryError("INVALID_REQUEST", str(err)) from None
        return "legacy", convert_legacy(data, metadata, None, limits=limits)


def reprojected(manifest: dict, query_code: str):
    """(stored bytes, stored manifest) from the trusted renderer."""
    try:
        return reproject(manifest, query_code=query_code)
    except UnsupportedProjection as exc:
        raise LibraryError("UNSUPPORTED_SAFE_PROJECTION", str(exc), 422) from None
    except ContractError as exc:
        raise LibraryError("CONTRACT_INVALID", f"conversion failed: {exc}", 422) from None


def preview_body(kind: str, stored: dict, *, existing: bool, duplicate) -> dict:
    return {"input": kind, "document_type": stored["document_type"],
            "document_id": None if kind == "legacy" else stored["document_id"],
            "outcome": ("duplicate" if duplicate else "new_version" if existing else "new_document"),
            "duplicate_of": duplicate,
            "title": stored["title"], "description": stored["description"], "tags": stored["tags"],
            "classification": stored["classification"], "generator": stored["generator"], "source": stored["source"],
            "native_schema": stored["projection"]["native_schema"],
            "query_code": stored["projection"]["options"]["query_code"],
            "omissions": stored["projection"]["omissions"],
            "coverage_warnings": stored["projection"]["coverage_warnings"],
            "object_count": len(stored["objects"]), "section_count": len(stored["sections"])}
