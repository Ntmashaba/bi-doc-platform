"""Pure orchestration: generate(request, progress, cancellation) -> result (handoff section 9).

Independent of HTTP, UI and Azure. Source paths are local configuration, never
untrusted request input.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from bidoc_contracts import ContractError, scope_key

from . import __version__
from .build import ADAPTERS, NotPublishable, build_artifact
from .identity import IdentityDecisionRequired, environment_key, resolve

ENGINE_KINDS = {"power_bi": ("abf", "pbix", "pbip", "tmdl", "bim", "pbir", "extracted"), "adf": ("adf_git", "adf_arm", "adf_resources")}
STAGES = ("validating", "analysing", "rendering", "completed")


@dataclass
class GenerateRequest:
    engine: str
    source_path: str
    source_kind: str
    output_dir: str
    profile: str = "local"                 # local | shared
    query_code: str = "withheld"           # shared profile only: withheld | included
    document_id: str | None = None         # explicitly reuse a known document identity
    identity_choice: str | None = None     # None | existing | new (for moved or copied sources)
    title: str | None = None
    description: str = ""
    tags: tuple = ()
    environment: str = ""                  # display label; the stream key is derived from it
    business_area: str = ""
    owner: str = ""
    mapping_dir: str = ""                  # local identity mapping when the source is read-only
    model_path: str | None = None           # explicit external model pairing
    extracted_path: str | None = None      # pbix only: the pbi-tools extract made from source_path


@dataclass
class GenerateResult:
    status: str                            # completed | local_only | failed | cancelled
    document_id: str | None = None
    revision_id: str | None = None
    artifact_path: str | None = None
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    tool_versions: dict = field(default_factory=dict)


class Cancelled(Exception):
    pass


def _safe_name(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", title).strip(" .")[:80] or "document"


def _sha256_of(path: Path):
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_atomically(path: Path, data: bytes) -> None:
    """Replace a previous output only once the new one is complete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def generate(request: GenerateRequest, progress=None, cancellation=None) -> GenerateResult:
    progress = progress or (lambda stage, message="": None)
    started, timings = time.monotonic(), {}
    result = GenerateResult(status="failed")

    def stage(name, message=""):
        if cancellation is not None and cancellation.is_set():
            raise Cancelled()
        timings[name] = round(time.monotonic() - started, 3)
        progress(name, message)

    def fail(code, message):
        result.errors.append({"code": code, "message": message})
        result.timings = timings
        return result

    adapter = ADAPTERS.get(request.engine)
    if adapter is None:
        return fail("INVALID_INPUT", f"unknown engine {request.engine!r}; use power_bi or adf")
    result.tool_versions = {"generator": __version__, adapter.ENGINE: adapter.ENGINE_VERSION}
    if request.source_kind not in ENGINE_KINDS[request.engine]:
        return fail("INVALID_INPUT", f"{request.engine} does not accept {request.source_kind!r} inputs; "
                                     f"use one of {', '.join(ENGINE_KINDS[request.engine])}")
    if request.profile not in ("local", "shared") or request.query_code not in ("withheld", "included"):
        return fail("INVALID_INPUT", "profile must be local or shared; query_code withheld or included")
    source = Path(request.source_path)
    try:
        stage("validating")
        if request.source_kind in ("pbix", "abf"):
            # The generator app extracts the PBIX (pbi-tools, child process) first. Identity,
            # label and hash stay with the PBIX; content comes from its extract.
            if not request.extracted_path or not source.is_file():
                raise adapter.InputError("a PBIX input needs the PBIX file and its pbi-tools extract")
            payload = adapter.load(Path(request.extracted_path), "extracted", request.title or source.stem, pbix=source, model_path=request.model_path)
        else:
            payload = (adapter.load(source, request.source_kind, request.title, model_path=request.model_path)
                       if request.engine == "power_bi" else adapter.load(source, request.source_kind, request.title))
        stage("analysing")
        descriptor, complete, _ = adapter.scope(payload)
        out_dir = Path(request.output_dir)
        if not complete or descriptor is None:
            # Local documentation still works; it just cannot be published.
            stage("rendering")
            suffix = ".shared.local.html" if request.profile == "shared" else ".local.html"
            path = out_dir / f"{_safe_name(payload.get('title', 'document'))}{suffix}"
            if request.profile == "shared":
                from .projection import project
                payload, _ = project(request.engine, payload, query_code=request.query_code)
            _write_atomically(path, adapter.render(payload).encode("utf-8"))
            result.status, result.artifact_path = "local_only", str(path)
            result.warnings.append("The input was not read completely, so the document has no publication "
                                   "manifest and cannot be published. See its coverage section.")
            stage("completed")
            result.timings = timings
            return result
        env_key = environment_key(request.environment)
        stream = f"{request.engine}|{env_key}|{scope_key(request.engine, descriptor)}"
        identity = resolve(source, stream, mapping_dir=request.mapping_dir or out_dir / ".bidoc-identity",
                           choice=request.identity_choice, document_id=request.document_id)
        stage("rendering")
        artifact, manifest = build_artifact(
            request.engine, payload, profile=request.profile, query_code=request.query_code, identity=identity,
            environment_key=env_key,
            classification={"business_area": request.business_area, "environment": request.environment,
                            "owner": request.owner},
            title=request.title or payload.get("title") or "", description=request.description,
            tags=request.tags, source_kind=request.source_kind, source_label=source.name,
            source_sha256=_sha256_of(source), generator_version=__version__)
        suffix = "" if request.profile == "local" else ".shared"
        path = out_dir / f"{_safe_name(manifest['title'])}--{identity.document_id[:8]}{suffix}.html"
        _write_atomically(path, artifact)
        result.status, result.artifact_path = "completed", str(path)
        result.document_id, result.revision_id = manifest["document_id"], manifest["revision_id"]
        result.warnings += manifest["projection"]["coverage_warnings"]
        if manifest["projection"]["omissions"]:
            result.warnings.append(f"{len(manifest['projection']['omissions'])} field(s) withheld or cleaned "
                                   "for shared publication; see the document's projection report.")
        stage("completed")
    except Cancelled:
        result.status = "cancelled"
    except IdentityDecisionRequired as exc:
        return fail("IDENTITY_DECISION_REQUIRED", str(exc))
    except (adapter.InputError, NotPublishable) as exc:
        return fail("INVALID_INPUT", str(exc))
    except ContractError as exc:
        return fail("CONTRACT_VIOLATION", f"{exc} {exc.issues[:3]}")
    except OSError as exc:
        return fail("IO_ERROR", str(exc))
    result.timings = timings
    return result
