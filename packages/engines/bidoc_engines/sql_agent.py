"""SQL Server Agent adapter over sqlagent-doc-gen (identity `sqlagent-identity/1`).

Envelope mapping (design v4 section 5.1): job -> `pipeline`, step -> `activity`
(parent = job), schedule -> `trigger` as a top-level object with no parent — a shared
schedule is many-to-many with jobs and the envelope has a single parent_object_id, so
the association lives in the payload and the rendered sections, exactly as ADF
triggers relate to pipelines.

Bindings reuse endpoints.py normalization; no new endpoint schema. SSAS processing
targets bind as `execute` on system "ssas" with a flat scalar invocation_context
(subsystem, process_type, process_object) — the envelope binding schema admits only
string | integer | boolean | null there. Harvested T-SQL binds read / write /
execute only: never delete, because the harvester does not classify statement kinds
beyond that. ProcessAdd / Process out-of-line binding sources bind as `read` on the
SQL endpoint.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import sqlagentdocgen
from sqlagentdocgen import details as sqlagent_details
from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.common import strip_brackets
from sqlagentdocgen.loader import load_path
from sqlagentdocgen.renderer import build_payload, render_html
from bidoc_contracts import ScopeError, sql_agent_scope

from . import endpoints
from .envelope import anchor, section

DOCUMENT_TYPE = sqlagent_details.DOCUMENT_TYPE
ENGINE = sqlagent_details.ENGINE
ENGINE_VERSION = sqlagentdocgen.__version__
IDENTITY_VERSION = sqlagent_details.IDENTITY_VERSION
SOURCE_KINDS = sqlagent_details.SOURCE_KINDS
VIEW_IDS = frozenset({"sqlagent.overview", "sqlagent.job", "sqlagent.step", "sqlagent.schedule"})
DETAILS_KEY = sqlagent_details.DETAILS_KEY

_SSAS_SUBSYSTEMS = ("ANALYSISCOMMAND", "ANALYSISQUERY")
# Operations whose out-of-line binding sources are relational reads (v4 §5.1).
_PROCESS_KINDS = ("process", "processadd")


class InputError(ValueError):
    pass


def validate_details(value) -> dict:
    raise ValueError("SQL Agent documents do not take a details file in this version")


def write_extras(payload: dict, html_path: Path, kinds) -> tuple[list, list]:
    """The engine's other outputs beside the document, from the payload the document was rendered from.

    Returns (paths written, notes): json, word, agent (context for LLM agents). The engine's CSV
    inventory uses its own file names, which the generator's per-document ownership record does not
    cover yet, so csv is declined here rather than written untracked.
    """
    import json
    written, notes = [], []
    for kind in kinds:
        if kind == "json":
            path = html_path.with_suffix(".json")
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(str(path))
        elif kind == "word":
            from sqlagentdocgen.word_writer import render_docx
            written.append(str(render_docx(payload, html_path.with_suffix(".docx"))))
        elif kind == "agent":
            from sqlagentdocgen.agent_writer import render_agent_md
            written.append(str(render_agent_md(payload, html_path.with_suffix(".agent.md"))))
        elif kind == "csv":
            raise ValueError("the csv output is not available for SQL Agent documents published "
                             "through the generator yet; generate it with sqlagent-doc-gen directly")
        else:
            raise ValueError(f"unknown output {kind!r}")
    return written, notes


def native_schema(payload) -> str:
    return f"{ENGINE}/{payload.get('schemaVersion')}"


def load(source: Path, kind: str, title: str | None = None) -> dict:
    source = Path(source)
    if kind not in SOURCE_KINDS:
        raise InputError(f"unsupported SQL Agent input kind {kind!r}; supported: {', '.join(SOURCE_KINDS)}")
    if not source.exists():
        raise InputError(f"input not found: {source}")
    if kind == "agent_script" and not source.is_file():
        raise InputError(f"an agent_script input must be a single .sql file: {source}")
    if kind == "agent_folder" and not source.is_dir():
        raise InputError(f"an agent_folder input must be a folder: {source}")
    try:
        analysis = analyse(load_path(source))
    except (OSError, ValueError) as exc:
        raise InputError(str(exc)) from exc
    stem = source.stem if source.is_file() else source.name
    name = title or stem.replace("_", " ").replace("-", " ").strip() or "SQL Agent jobs"
    return build_payload(analysis, name)


def scope(payload):
    """(descriptor, complete, coverage warnings).

    The scope is always a selection of jobs (v4 §2): identity is the scripted GUID where
    present, else the job name. A file that could not be read at all means the declared
    scope was not fully read: not complete. Skipped files are .sql files that are not job
    scripts — a legitimate scoping decision, not an incomplete read — so they are a
    coverage warning, as are unparsed steps and unknown operations.
    """
    cov = payload.get("coverage") or {}
    identities = [j.get("identity") for j in payload.get("jobs", [])]
    if any(not i or i.endswith(":") for i in identities):
        # A job with neither a scripted GUID nor a name has no stream identity at all.
        return None, False, ["A job with no name and no scripted GUID cannot take part in a "
                             "publication scope."]
    warnings = []
    if cov.get("loadErrors"):
        warnings.append(f"{cov['loadErrors']} file(s) could not be read.")
    if cov.get("skippedFiles"):
        warnings.append(f"{len(cov['skippedFiles'])} .sql file(s) were not job scripts and were skipped.")
    if cov.get("unrecognisedStatements"):
        warnings.append(f"{cov['unrecognisedStatements']} EXEC statement(s) were not recognised.")
    if cov.get("unparsedSteps"):
        warnings.append(f"{cov['unparsedSteps']} step(s) could not be parsed.")
    if cov.get("unknownOperations"):
        warnings.append(f"{cov['unknownOperations']} processing operation(s) were not recognised.")
    if not identities:
        return None, False, warnings
    try:
        descriptor = sql_agent_scope(identities)
    except ScopeError as exc:
        return None, False, warnings + [str(exc)]
    return descriptor, not cov.get("loadErrors"), warnings


def render(payload) -> str:
    with tempfile.TemporaryDirectory() as d:
        out = render_html(payload, Path(d) / "document.html")
        return out.read_text(encoding="utf-8")


def _split_dotted(name: str):
    """'[dbo].[Table]' -> ('dbo', 'Table'); 'Table' -> (None, 'Table')."""
    parts = [strip_brackets(p) for p in (name or "").split(".") if p]
    if len(parts) >= 2:
        return ".".join(parts[:-1]), parts[-1]
    return None, (parts[0] if parts else name)


def job_object_id(identity: str) -> str:
    return f"sqlagent:job:{identity}"


def step_object_id(identity: str, step_id) -> str:
    return f"sqlagent:step:{identity}/{step_id}"


def schedule_object_id(key: str) -> str:
    return f"sqlagent:schedule:{key}"


def _schedule_key(s: dict) -> str:
    if s.get("uid"):
        return f"uid:{s['uid']}"
    if s.get("id") is not None:
        return f"id:{s['id']}"
    return f"name:{(s.get('name') or '').strip().lower()}"


def _binding(bid: str, operation: str, ep: dict, context: dict, ref: str,
             resolution: str, coverage: str) -> dict:
    return {"binding_id": bid[:512], "operation": operation, "endpoint": ep,
            "normalized_endpoint": endpoints.normalize(ep),
            "normalization_version": endpoints.NORMALIZATION_VERSION,
            "invocation_context": context, "evidence_refs": [ref],
            "resolution": resolution, "coverage": coverage}


def _step_bindings(job: dict, step: dict, ref: str, coverage: str) -> list:
    """Endpoint bindings for one step; empty when the command is not a static literal."""
    out = []
    sub = (step.get("subsystem") or "TSQL").upper()
    sid = step_object_id(job.get("identity", ""), step.get("id"))
    resolution = "opaque" if step.get("parseError") else "static"
    n = 0

    def add(operation, ep, context):
        nonlocal n
        out.append(_binding(f"{sid}#{operation}:{n}", operation, ep, context, ref, resolution, coverage))
        n += 1

    if sub in _SSAS_SUBSYSTEMS:
        server = step.get("server")
        for op in step.get("operations", []):
            # flat scalars only: the envelope's invocation_context admits nothing else
            context = {"subsystem": sub,
                       "process_type": op.get("verb") or op.get("kind"),
                       "process_object": op.get("object")}
            objpath = op.get("object")
            if objpath:
                parts = [p for p in objpath.split("/") if p]
                if parts:
                    add("execute", endpoints.endpoint(system="ssas", server=server, database=parts[0],
                                                      object="/".join(parts[1:]) or None), context)
            for source in op.get("reads", []):
                if (op.get("kind") or "").lower() in _PROCESS_KINDS:
                    # out-of-line binding source (the query/table behind the data source id)
                    add("read", endpoints.endpoint(system="sql", object=source), context)
                else:
                    # e.g. a Synchronize / Restore source database: an SSAS-side read
                    add("read", endpoints.endpoint(system="ssas", server=server, database=source), context)
    elif sub == "TSQL":
        db = step.get("database")
        context = {"subsystem": sub, "database": db}
        sql = step.get("sql") or {}
        # read / write / execute only — never delete: the harvester does not classify
        # statement kinds beyond that, and a binding must not claim knowledge it lacks.
        for table in sql.get("reads", []):
            sch, obj = _split_dotted(table)
            add("read", endpoints.endpoint(system="sql", database=db, schema=sch, object=obj), context)
        for table in sql.get("writes", []):
            sch, obj = _split_dotted(table)
            add("write", endpoints.endpoint(system="sql", database=db, schema=sch, object=obj), context)
        for proc in sql.get("procs", []):
            sch, obj = _split_dotted(proc)
            add("execute", endpoints.endpoint(system="sql", database=db, schema=sch, object=obj), context)
    return out


def describe(payload: dict, coverage: str = "complete"):
    objects, sections, targets = [], [], []
    overview = "pbidoc-overview"
    cov = payload.get("coverage") or {}
    jobs = payload.get("jobs", [])
    steps_n = sum(len(j.get("steps", [])) for j in jobs)
    schedules = {}
    for j in jobs:
        for s in j.get("schedules", []):
            entry = schedules.setdefault(_schedule_key(s), {"schedule": s, "jobs": set()})
            entry["jobs"].add(j.get("name") or j.get("identity") or "")
    for s in payload.get("sharedSchedules", []):
        entry = schedules.setdefault(_schedule_key(s), {"schedule": s, "jobs": set()})
        entry["jobs"].update(s.get("attachedJobs") or [])

    sections.append(section(overview, payload.get("title"), [
        "SQL Server Agent job documentation (static analysis of job scripts; "
        "the engine never connected to a server).",
        f"{len(jobs)} jobs, {steps_n} steps, {len(schedules)} schedules, "
        f"{len(payload.get('issues', []))} findings.",
        f"Input files analysed: {cov.get('inputFiles', 0)}; skipped: {len(cov.get('skippedFiles') or [])}; "
        f"unparsed steps: {cov.get('unparsedSteps', 0)}."]))
    targets.append({"target_id": overview, "view_id": "sqlagent.overview", "args": {}})

    for ji, j in enumerate(jobs):
        identity = j.get("identity") or ""
        jid = job_object_id(identity)
        jsid = anchor("job", jid)
        label = (j.get("name") or identity)[:512]
        objects.append({"object_id": jid[:512], "kind": "pipeline", "label": label, "section_id": jsid,
                        "parent_object_id": None, "bindings": [], "dynamic": False, "opaque": False,
                        "coverage": coverage})
        sections.append(section(jsid, f"Job {label}", [
            j.get("description"),
            f"Enabled: {j.get('enabled')}",
            f"Owner: {j.get('owner')}" if j.get("owner") else "",
            f"Category: {j.get('category')}" if j.get("category") else "",
            "Steps: " + ", ".join(str(s.get("name") or s.get("id")) for s in j.get("steps", [])),
            "Schedules: " + ", ".join(str(s.get("name") or "") for s in j.get("schedules", []))
            if j.get("schedules") else ""]))
        targets.append({"target_id": jid[:512], "view_id": "sqlagent.job", "args": {"job": label}})

        for si, st in enumerate(j.get("steps", [])):
            sid = step_object_id(identity, st.get("id"))
            ssid = anchor("step", sid)
            own = _step_bindings(j, st, f"/jobs/{ji}/steps/{si}", coverage)
            sub = (st.get("subsystem") or "TSQL").upper()
            objects.append({"object_id": sid[:512], "kind": "activity",
                            "label": (st.get("name") or f"step {st.get('id')}")[:512],
                            "section_id": ssid, "parent_object_id": jid[:512], "bindings": own,
                            "dynamic": st.get("commandKind") in ("variable", "expression"),
                            "opaque": bool(st.get("parseError")) or st.get("commandKind") == "missing",
                            "coverage": coverage})
            sections.append(section(ssid, f"Step {st.get('id')} {st.get('name') or ''}".strip(), [
                f"Job: {label}", f"Subsystem: {sub}",
                f"Target database: {st.get('database')}" if st.get("database") else "",
                f"Target server: {st.get('server')}" if st.get("server") else "",
                f"Runs as proxy: {st.get('proxy')}" if st.get("proxy") else "",
                f"On success: {st.get('onSuccessAction')}; on failure: {st.get('onFailAction')}",
                f"Retries: {st.get('retryAttempts')} every {st.get('retryIntervalMinutes')} minute(s)"
                if st.get("retryAttempts") else "",
                "Operations: " + "; ".join(
                    f"{o.get('verb') or o.get('kind')} {o.get('object') or ''}".strip()
                    for o in st.get("operations", [])) if st.get("operations") else ""]))
            targets.append({"target_id": sid[:512], "view_id": "sqlagent.step",
                            "args": {"job": label, "step": st.get("name") or str(st.get("id"))}})

    for key, entry in sorted(schedules.items()):
        s, attached = entry["schedule"], sorted(entry["jobs"])
        tid = schedule_object_id(key)
        tsid = anchor("sched", tid)
        objects.append({"object_id": tid[:512], "kind": "trigger",
                        "label": (s.get("name") or key)[:512], "section_id": tsid,
                        # shared schedules are many-to-many with jobs: no single parent
                        "parent_object_id": None, "bindings": [], "dynamic": False, "opaque": False,
                        "coverage": coverage})
        sections.append(section(tsid, f"Schedule {s.get('name') or key}", [
            f"Enabled: {s.get('enabled')}",
            s.get("summary"),
            f"Active {s.get('activeStartDate')} to {s.get('activeEndDate')}"
            if s.get("activeStartDate") else "",
            "Applies to: " + ", ".join(attached) if attached else ""]))
        targets.append({"target_id": tid[:512], "view_id": "sqlagent.schedule",
                        "args": {"schedule": s.get("name") or key}})
    return objects, sections, targets
