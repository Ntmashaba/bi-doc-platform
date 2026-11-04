"""Pure orchestration: generate(request, progress, cancellation) -> result (handoff section 9).

Independent of HTTP, UI and Azure. Source paths are local configuration, never
untrusted request input.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from bidoc_contracts import ContractError, Limits, scope_key

from . import __version__
from .build import ADAPTERS, NotPublishable, build_artifact
from .envelope import GENERATION_LIMITS, publication_problem, publication_warning, size_breakdown
from .identity import IdentityDecisionRequired, environment_key, resolve

# Details a document cannot read from its source (Power BI: report location, catalogue folder, connection account
# references; Data Factory: owner, environment, runbook and the like) are recorded before the document is sealed, so they
# survive the integrity check that makes the page read-only. They come from the request's file or, without one, from a
# file beside the source, like the identity sidecar: <source name>.bidoc-details.json. That file stays with the source,
# so every regeneration (generate, batch, the desktop app) records the same details.
DETAILS_SIDECAR = ".bidoc-details.json"


def details_sidecar(source: Path) -> Path:
    return source.with_name(source.name + DETAILS_SIDECAR)


# The engines' other outputs, written beside the document when asked for. Each is made from the payload the document
# was rendered from, so a shared copy's extras carry exactly what its document does (projected, query code withheld
# unless included). They are files for local use: they carry no manifest and the library does not take them.
#
# Each output is written in a private folder beside the document and moved into place only once it is complete (the
# four Data Factory CSVs move together). A record beside the document, <document>.outputs.json, lists what this run
# wrote, with the document's identity and revision. The next run over the same document removes the extras the record
# lists that it did not write again: one no longer asked for, or one that failed and so cannot be current. Files the
# record does not list (written by generate_docs.py, or by anyone else) are never touched.
EXTRAS = ("json", "word", "csv", "agent")
_EXTRA_NAMES = {"json": "JSON extract", "word": "Word document", "csv": "CSV", "agent": "agent context"}
OUTPUTS_RECORD = ".outputs.json"
_RECORD_SCHEMA = "bidoc-outputs/1"


def _owned_names(stem: str) -> set:
    """Every name an extra of the document <stem>.html can have, relative to its folder."""
    return ({f"{stem}.json", f"{stem}.docx", f"{stem}.columns.csv", f"{stem}.agent.md"}
            | {f"{stem}-csv/{n}.csv" for n in ("objects", "usage", "edges", "issues")})


def _cause(exc: BaseException) -> str:
    """An error in general terms: its type and, for a system error, the system's description. Never the exception's
    own text, which can quote a value or a local path (an OSError keeps the file name apart from its strerror)."""
    cause = type(exc).__name__
    if isinstance(exc, OSError) and exc.strerror:
        cause += f", {exc.strerror}"
    return cause


def _failure(kind: str, exc: BaseException) -> str:
    return (f"The {_EXTRA_NAMES[kind]} could not be written ({_cause(exc)}). The document and any other outputs were "
            f"written; run again to retry.")


# File-system steps, named so tests can make one fail.
def _replace(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _rename(src: Path, dst: Path) -> None:
    os.rename(src, dst)


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)


def _read_record(record: Path, document: Path) -> dict | None:
    """This document's outputs record, or None when there is none, or the file is not one written for it."""
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != _RECORD_SCHEMA or data.get("document") != document.name:
        return None
    return data


def _publish_folder(work: Path, folder: Path, name: str, owned: set) -> None:
    """Put the folder work/<name> in place of folder/<name> as a whole, so it never mixes two runs' files.

    The old folder (only ever this document's own files) is moved aside, the new one renamed in, and the old one
    discarded. If the new one cannot be put in place, the old one is put back and the caller treats it as stale.
    """
    import shutil
    import tempfile
    final, built = folder / name, work / name
    if final.exists():
        inside = {f"{name}/{p.name}" for p in final.iterdir()}
        if not final.is_dir() or not inside <= owned or any(p.is_dir() for p in final.iterdir()):
            raise FileExistsError(17, "The folder holds other files, so it was not replaced")
        aside = Path(tempfile.mkdtemp(prefix=".bidoc-extras-old-", dir=folder)) / name
        try:
            _rename(final, aside)
        except BaseException:
            shutil.rmtree(aside.parent, ignore_errors=True)
            raise
        try:
            _rename(built, final)
        except BaseException:
            try:
                _rename(aside, final)        # put the earlier folder back; the caller then removes it as stale
            except OSError:
                pass                         # it was only this document's own earlier CSVs: discard them below
            shutil.rmtree(aside.parent, ignore_errors=True)
            raise
        shutil.rmtree(aside.parent, ignore_errors=True)
    else:
        _rename(built, final)


def _write_extras(adapter, payload: dict, path: Path, kinds, result, profile: str) -> None:
    """Write the extras; whatever happens here, the document already written stays a success."""
    try:
        _write_extras_unguarded(adapter, payload, path, kinds, result, profile)
    except Exception as exc:                     # a last line: never fail a written document over its extras
        result.warnings.append(f"The other outputs could not be finished ({_cause(exc)}). The document was written; "
                               f"run again to retry.")


def _write_extras_unguarded(adapter, payload: dict, path: Path, kinds, result, profile: str) -> None:
    import shutil
    import tempfile
    folder = path.parent
    record = folder / f"{path.stem}{OUTPUTS_RECORD}"
    earlier = _read_record(record, path)
    owned = _owned_names(path.stem)
    previous = {o for o in (earlier or {}).get("outputs") or [] if isinstance(o, str) and o in owned}
    current, failed = [], []
    for kind in kinds:
        work = Path(tempfile.mkdtemp(prefix=".bidoc-extras-", dir=folder))
        try:
            try:
                written, notes = adapter.write_extras(payload, work / path.name, (kind,))
                made = [Path(w).relative_to(work).as_posix() for w in written]
                if any(m not in owned for m in made):
                    raise ValueError("an output was written under a name that is not one of the document's extras")
                files = [m for m in made if "/" not in m]
                folders = sorted({m.split("/", 1)[0] for m in made if "/" in m})
                for relative in files:           # one file: a single atomic replace
                    _replace(work / relative, folder / relative)
                for name in folders:             # several files: the folder is replaced whole
                    _publish_folder(work, folder, name, owned)
            except Exception as exc:             # an extra never costs the document or the other extras
                result.warnings.append(_failure(kind, exc))
                failed.append(kind)
                continue
            result.extra_paths += [str(folder / m) for m in made]
            current += made
            result.warnings += notes
        finally:
            shutil.rmtree(work, ignore_errors=True)
    stale = sorted(previous - set(current))
    kept = []
    for relative in stale:                       # only this document's own extras, as its record lists them
        target = folder / relative
        try:
            _remove(target)
        except OSError as exc:
            kept.append(relative)
            result.warnings.append(f"An earlier output, {relative}, could not be removed ({_cause(exc)}); it is "
                                   f"older than the document. Remove it, or run again.")
            continue
        if target.parent != folder:
            try:
                target.parent.rmdir()            # the CSV folder, once nothing is left in it
            except OSError:
                pass
    if failed and len(kept) < len(stale):
        result.warnings.append("Removed the earlier copy of each output that could not be written this time, so "
                               "no file beside the document is older than it.")
    listed = current + kept                      # still ours: the next run tries again to remove what stayed
    try:
        if listed:
            text = json.dumps({"schema": _RECORD_SCHEMA, "document": path.name, "document_id": result.document_id,
                               "revision_id": result.revision_id, "profile": profile, "generated_at": _now(),
                               "outputs": listed, "stale": kept}, indent=1)
            fd, tmp = tempfile.mkstemp(prefix=".bidoc-extras-", suffix=".json", dir=folder)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                _replace(Path(tmp), record)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        elif earlier is not None:
            _remove(record)                      # nothing beside the document is ours any more
    except OSError as exc:
        result.warnings.append(f"The record of this document's other outputs could not be updated ({_cause(exc)}). "
                               f"The document and its outputs were written; a later run may not remove outputs this "
                               f"one wrote.")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
    model_path: str | None = None           # external model pairing, ready to read (an ABF or PBIX already extracted)
    model_selected_by: str = ""            # how it was chosen: "--model", or the details file that names it
    model_name: str = ""                   # the model as supplied (file or folder name), for the document
    extracted_path: str | None = None      # pbix only: the pbi-tools extract made from source_path
    bidoc_version: str | None = None       # the bidoc generator running this request; shown in the document
    details_path: str | None = None        # details JSON; without it, <source name>.bidoc-details.json if present
    also: tuple = ()                       # other outputs beside the document: any of EXTRAS


PUBLICATION_LIMITS = Limits()      # what the library accepts by default; checked after the document is written


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
    publication: dict | None = None        # set when the document was written but the library would reject it
    extra_paths: list = field(default_factory=list)   # the other outputs written beside the document


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


MODEL_SUFFIXES = (".abf", ".pbix", ".bim")      # a model file a report can be paired with; or a model folder (TMDL, model.bim)
_NEEDS_EXTRACTION = (".abf", ".pbix")


def _load_details_file(adapter, path: Path) -> tuple[dict | None, dict | None]:
    """(documentation details, model pairing) from a details file; any problem names the file and stops generation.

    Two forms. Flat: the engine's documentation fields at the top (`{"owner": ...}`). Structured, when the file also pairs
    a Power BI report with a model: `{"documentation": {...}, "model": {"path": "..."}}`, either part optional. A flat
    file that also has "model" is refused rather than read one way or the other.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise adapter.InputError(f"details file not found: {path}") from None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise adapter.InputError(f"details file {path.name} could not be read as JSON: {exc}") from None
    model, structured = None, isinstance(raw, dict) and bool(raw) and set(raw) <= {"documentation", "model"}
    if structured:
        documentation, model = raw.get("documentation"), raw.get("model")
    else:
        documentation = raw
        if isinstance(raw, dict) and "model" in raw:
            raise adapter.InputError(f'details file {path.name}: a file that pairs a model keeps its documentation fields '
                                     f'under "documentation": {{"documentation": {{...}}, "model": {{"path": "..."}}}}')
    if model is not None:
        if adapter.DOCUMENT_TYPE != "power_bi":
            raise adapter.InputError(f"details file {path.name}: only a Power BI report is paired with a model")
        if not (isinstance(model, dict) and set(model) == {"path"} and isinstance(model["path"], str) and model["path"].strip()):
            raise adapter.InputError(f'details file {path.name}: "model" must be {{"path": "<model file or folder>"}}')
    try:
        # Only the structured form may leave the documentation out; a flat file is always the details themselves.
        details = adapter.validate_details(documentation) if documentation is not None or not structured else None
    except ValueError as exc:
        raise adapter.InputError(f"details file {path.name}: {exc}") from None
    return details, model


def _details_file(request_path: str | None, source: Path) -> Path | None:
    """The details file that applies: the one named on the request, else <source name>.bidoc-details.json if present."""
    if request_path:
        return Path(request_path)
    side = details_sidecar(source)
    return side if side.is_file() else None


@dataclass
class Pairing:
    path: str | None = None                # the model to pair, resolved; None for no pairing
    selected_by: str = ""                  # "--model", or the details file's name
    name: str = ""                         # the model's file or folder name, for the document
    notes: list = field(default_factory=list)

    @property
    def needs_extraction(self) -> bool:
        return bool(self.path) and Path(self.path).suffix.lower() in _NEEDS_EXTRACTION


def _check_model(path: Path, said: str, where: str, error) -> None:
    if not path.exists():
        raise error(f"{where} pairs the model {said}, which was not found (looked for {path})")
    if path.is_file():
        if path.suffix.lower() not in MODEL_SUFFIXES:
            raise error(f"{where} pairs the model {said}, which is not a supported model: use an ABF backup, a PBIX, a "
                        f".bim file, or a model folder (TMDL or model.bim)")
        try:
            with path.open("rb"):
                pass
        except OSError as exc:
            raise error(f"{where} pairs the model {said}, which cannot be read ({type(exc).__name__})") from None
        return
    from pbidocgen.tmdl_reader import find_definition_dir  # noqa: PLC0415
    if not ((path / "model.bim").is_file() or (path / "definition" / "model.bim").is_file() or find_definition_dir(path)):
        raise error(f"{where} pairs the model {said}, a folder with no model.bim or TMDL definition in it")


def resolve_pairing(engine: str, source: Path, details_path: str | None = None, explicit: str | None = None) -> Pairing:
    """The model a Power BI report is paired with, decided once for generate, batch, retry and the desktop app.

    An explicit model (`--model`) wins; a model named in the report's details file is used otherwise. A relative path in
    that file is resolved against the file's own folder (beside the report, that is the report's folder), never the
    current directory. Nothing is ever guessed from file names. The pairing is checked before anything is generated:
    the model must exist, be readable and be a supported format. It is never verified against the report's live
    connection, and the document says so.
    """
    from .power_bi import InputError  # noqa: PLC0415
    pairing = Pairing()
    if engine != "power_bi":
        if explicit:
            raise InputError("only a Power BI report is paired with a model")
        return pairing
    file = _details_file(details_path, source)
    named = None
    if file is not None:
        from . import power_bi  # noqa: PLC0415
        named = _load_details_file(power_bi, file)[1]
    if explicit:
        model = Path(explicit)
        _check_model(model, explicit, "--model", InputError)
        if named:
            pairing.notes.append(f"--model was given, so the model {named['path']!r} named in {file.name} is not used.")
        return Pairing(str(model.resolve()), "--model", model.name, pairing.notes)
    if not named:
        return pairing
    model = Path(named["path"].strip())
    model = model if model.is_absolute() else (file.parent / model)
    _check_model(model, repr(named["path"]), file.name, InputError)
    model = model.resolve()
    return Pairing(str(model), file.name, model.name)


def _read_details(adapter, path: Path) -> dict | None:
    """The documentation details in `path`, checked by the engine's own rules (None when the file only pairs a model)."""
    return _load_details_file(adapter, path)[0]


def _environment_note(request: GenerateRequest, details: dict, path: Path) -> str | None:
    """Factory details carry an `environment` for readers; the request's environment files the document in the
    library and is part of its identity. One never sets the other. Say so when they disagree, or when only the
    details name one, so a reader does not assume the document was filed under it."""
    written = str(details.get("environment") or "").strip() if request.engine == "adf" else ""
    filed = (request.environment or "").strip()
    if not written or written.casefold() == filed.casefold():
        return None
    if not filed:
        return (f"{path.name} gives the environment {written!r} for readers of the factory details, but the "
                f"document is filed with no environment. Use --environment {written!r} to file it there; the "
                f"environment is part of the document's identity.")
    return (f"{path.name} gives the environment {written!r}, but the document is filed under {filed!r} "
            f"(--environment). The factory details show {written!r}; the library and the document identity use "
            f"{filed!r}.")


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
    unknown = [k for k in request.also if k not in EXTRAS]
    if unknown:
        return fail("INVALID_INPUT", f"unknown output {', '.join(map(repr, unknown))}; use any of {', '.join(EXTRAS)}")
    also = tuple(dict.fromkeys(request.also))
    source = Path(request.source_path)
    try:
        stage("validating")
        model_path, pairing = request.model_path, None
        if request.engine == "power_bi":
            if model_path:                       # prepared by the caller (bidoc generate, batch, retry, desktop)
                pairing = {"model": request.model_name or Path(model_path).name,
                           "selectedBy": request.model_selected_by or "--model"}
            else:                                # a caller that did not prepare one: the details file's own pairing
                found = resolve_pairing("power_bi", source, request.details_path)
                if found.needs_extraction:
                    raise adapter.InputError(f"{found.selected_by} pairs the model {found.name}, which has to be "
                                             f"extracted first; bidoc generate and bidoc batch do that")
                if found.path:
                    model_path, pairing = found.path, {"model": found.name, "selectedBy": found.selected_by}
        if request.source_kind in ("pbix", "abf"):
            # The generator app extracts the PBIX (pbi-tools, child process) first. Identity,
            # label and hash stay with the PBIX; content comes from its extract.
            if not request.extracted_path or not source.is_file():
                raise adapter.InputError("a PBIX input needs the PBIX file and its pbi-tools extract")
            payload = adapter.load(Path(request.extracted_path), "extracted", request.title or source.stem, pbix=source,
                                   model_path=model_path, pairing=pairing)
        else:
            payload = (adapter.load(source, request.source_kind, request.title, model_path=model_path, pairing=pairing)
                       if request.engine == "power_bi" else adapter.load(source, request.source_kind, request.title))
        details_file = _details_file(request.details_path, source)
        if details_file is not None:
            details = _read_details(adapter, details_file)
            if details is not None:
                payload[adapter.DETAILS_KEY] = details
                progress("validating", f"details from {details_file.name}")
                note = _environment_note(request, details, details_file)
                if note:
                    result.warnings.append(note)
        if isinstance(payload.get("producer"), dict):
            # The document says what generated it, beside its generation time.
            payload["producer"]["bidoc"] = request.bidoc_version or None
        stage("analysing")
        descriptor, complete, _ = adapter.scope(payload)
        out_dir = Path(request.output_dir)
        if not complete or descriptor is None:
            # Local documentation still works; it just cannot be published.
            stage("rendering")
            suffix = ".shared.local.html" if request.profile == "shared" else ".local.html"
            # A document that cannot be published has no identity to tell it from another with the same title, so the name
            # carries a short tag of the source path: two such inputs called Sales.pbix in different folders no longer share
            # one output file, and the same input still maps to the same file when it is run again.
            tag = hashlib.sha256(os.path.normcase(os.path.realpath(request.source_path)).encode("utf-8")).hexdigest()[:8]
            path = out_dir / f"{_safe_name(payload.get('title', 'document'))}--{tag}{suffix}"
            if request.profile == "shared":
                from .projection import project
                payload, _ = project(request.engine, payload, query_code=request.query_code)
            rendered = adapter.render(payload).encode("utf-8")
            if len(rendered) > GENERATION_LIMITS.html_bytes:
                raise ContractError("ARTIFACT_TOO_LARGE", f"The rendered document is {len(rendered)} bytes, above the "
                                                          f"{GENERATION_LIMITS.html_bytes}-byte generation ceiling.")
            _write_atomically(path, rendered)
            result.status, result.artifact_path = "local_only", str(path)
            _write_extras(adapter, payload, path, also, result, request.profile)
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
        _write_extras(adapter, manifest["native_payload"]["data"], path, also, result, request.profile)
        problem = publication_problem(artifact, adapter.VIEW_IDS, PUBLICATION_LIMITS)
        if problem:
            # Written and usable locally; only publication is refused.
            problem["operation"] = "publish"
            problem["breakdown_bytes"] = size_breakdown(artifact, manifest)
            result.publication = problem
            result.status = "local_only"
            result.warnings.append(publication_warning(problem, path.name))
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
