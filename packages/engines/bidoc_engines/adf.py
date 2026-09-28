"""Azure Data Factory adapter over adf-doc-gen (identity `adf-identity/1`)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import adfdocgen
from adfdocgen.analyzer import analyze
from adfdocgen.loader import collect_inputs
from adfdocgen.renderer import build_payload, render_html
from bidoc_contracts import adf_scope

from . import endpoints
from .envelope import anchor, section

DOCUMENT_TYPE = "adf"
ENGINE = "adf-doc-gen"
ENGINE_VERSION = adfdocgen.__version__
IDENTITY_VERSION = "adf-identity/1"
SOURCE_KINDS = ("adf_git", "adf_arm", "adf_resources")
VIEW_IDS = frozenset({"adf.overview", "adf.pipeline", "adf.activity", "adf.trigger", "adf.dataflow"})
OPERATIONS = {"read": "read", "write": "write", "delete": "delete", "execute": "execute", "executed": "execute"}


class InputError(ValueError):
    pass


def native_schema(payload) -> str:
    return f"{ENGINE}/{payload.get('schemaVersion')}"


def load(source: Path, kind: str, title: str | None = None) -> dict:
    source = Path(source)
    if kind not in SOURCE_KINDS:
        raise InputError(f"unsupported ADF input kind {kind!r}; supported: {', '.join(SOURCE_KINDS)}")
    if not source.exists():
        raise InputError(f"input not found: {source}")
    try:
        store = collect_inputs(str(source))
        analysis = analyze(store)
    except (OSError, ValueError) as exc:
        raise InputError(str(exc)) from exc
    stem = source.stem if source.is_file() else source.name
    name = title or stem.replace("_", " ").replace("-", " ").strip() or "Data Factory"
    return build_payload(analysis, name)


def scope(payload):
    """(descriptor, complete, coverage warnings).

    Skipped input files mean the declared scope was not fully read: not complete.
    Unresolved references inside a fully read scope are coverage warnings.
    """
    cov = payload.get("coverage") or {}
    complete = not cov.get("skippedFiles")
    whole = bool(set(cov.get("inputFormats") or []) & {"ARM template", "Git folder"})
    resources = ([f"pipeline/{p['name']}" for p in payload.get("pipelines", [])]
                 + [f"dataset/{d['name']}" for d in payload.get("datasets", [])]
                 + [f"linkedService/{x['name']}" for x in payload.get("linkedServices", [])]
                 + [f"dataflow/{d['name']}" for d in payload.get("dataflows", [])]
                 + [f"trigger/{t['name']}" for t in payload.get("triggers", [])])
    warnings = []
    if cov.get("unresolvedReferences"):
        warnings.append(f"{cov['unresolvedReferences']} reference(s) point to resources not in the input.")
    if cov.get("unrecognised"):
        warnings.append(f"{len(cov['unrecognised'])} JSON file(s) were not recognised as Data Factory resources.")
    if cov.get("opaqueActivities"):
        warnings.append(f"{cov['opaqueActivities']} activity(ies) are opaque: their data footprint is not visible.")
    if cov.get("dynamicActivities"):
        warnings.append(f"{cov['dynamicActivities']} activity(ies) resolve their targets at runtime.")
    if cov.get("runtimeHealth"):
        warnings.append(f"Runtime health {cov['runtimeHealth']}.")
    if not whole and not resources:
        return None, False, warnings
    return (adf_scope(True) if whole else adf_scope(False, resources)), complete, warnings


def render(payload) -> str:
    with tempfile.TemporaryDirectory() as d:
        out = render_html(payload, Path(d) / "document.html")
        return out.read_text(encoding="utf-8")


def _endpoint(ep: dict) -> dict:
    ep = ep or {}
    host, instance, port = endpoints.split_server(ep.get("server"))
    url = ep.get("url")
    account = endpoints.storage_account_of(url or ep.get("server"))
    return endpoints.endpoint(system=ep.get("system"), server=host if not account else None, instance=instance,
                              port=ep.get("port") or port, database=ep.get("database"), schema=ep.get("schema"),
                              object=ep.get("object"), storage_account=account, container=ep.get("container"),
                              path=ep.get("path"), url=url)


def activity_id(pipeline: str, activity: str) -> str:
    # Activity names are unique within a pipeline, including nested containers.
    return f"adf:activity:{pipeline}/{activity}"


def describe(payload: dict, coverage: str = "complete"):
    objects, sections, targets = [], [], []
    overview = "pbidoc-overview"
    cov = payload.get("coverage") or {}
    sections.append(section(overview, payload.get("title"), [
        f"Data Factory documentation ({payload.get('mode')}).",
        f"{len(payload.get('pipelines', []))} pipelines, {cov.get('activities', 0)} activities, "
        f"{len(payload.get('triggers', []))} triggers, {len(payload.get('dataflows', []))} data flows."]))
    targets.append({"target_id": overview, "view_id": "adf.overview", "args": {}})

    entities = {e["key"]: (i, e) for i, e in enumerate(payload.get("entities", []))}
    bindings: dict[str, list] = {}
    for key, (i, e) in entities.items():
        if e.get("kind") not in ("table", "file_path", "stored_procedure", "dataset", "inline_dataset"):
            continue
        ep = _endpoint(e.get("endpoint"))
        for u in e.get("usage", []):
            op = OPERATIONS.get(u.get("operation"), "read")
            resolution = "opaque" if u.get("opaque") else "dynamic" if (u.get("dynamic") or e.get("dynamic")) else "static"
            aid = activity_id(u.get("pipeline", ""), u.get("activity", ""))
            bindings.setdefault(aid, []).append({
                "binding_id": f"{aid}#{op}:{key}"[:512], "operation": op, "endpoint": ep,
                "normalized_endpoint": endpoints.normalize(ep),
                "normalization_version": endpoints.NORMALIZATION_VERSION,
                "invocation_context": {"pipeline": u.get("pipeline"), "activity": u.get("activity"),
                                       "activity_type": u.get("type")},
                "evidence_refs": [f"/entities/{i}"], "resolution": resolution, "coverage": coverage})
    labels = {k: e.get("label", k) for k, (_, e) in entities.items()}

    for p in payload.get("pipelines", []):
        pid = f"adf:pipeline:{p['name']}"
        psid = anchor("pl", pid)
        objects.append({"object_id": pid, "kind": "pipeline", "label": p["name"][:512], "section_id": psid,
                        "parent_object_id": None, "bindings": [], "dynamic": bool(p.get("dynamicCount")),
                        "opaque": bool(p.get("opaqueCount")), "coverage": coverage})
        sections.append(section(psid, f"Pipeline {p['name']}", [
            p.get("description"), p.get("roleLine"),
            "Activities: " + ", ".join(a["activity"] for a in p.get("activities", [])),
            "Started by: " + ", ".join(p.get("startedBy") or []) if p.get("startedBy") else ""]))
        targets.append({"target_id": pid, "view_id": "adf.pipeline", "args": {"pipeline": p["name"]}})
        for a in p.get("activities", []):
            aid = activity_id(p["name"], a["activity"])
            asid = anchor("a", aid)
            own = bindings.get(aid, [])
            objects.append({"object_id": aid[:512], "kind": "activity", "label": a["activity"][:512],
                            "section_id": asid, "parent_object_id": pid, "bindings": own,
                            "dynamic": any(b["resolution"] == "dynamic" for b in own),
                            "opaque": any(b["resolution"] == "opaque" for b in own), "coverage": coverage})
            sections.append(section(asid, f"Activity {a['activity']}", [
                f"Pipeline: {p['name']}", f"Type: {a.get('type')}", a.get("description"),
                "Reads: " + ", ".join(labels.get(k, k) for k in a.get("reads", [])) if a.get("reads") else "",
                "Writes: " + ", ".join(labels.get(k, k) for k in a.get("writes", [])) if a.get("writes") else "",
                a.get("detail"), a.get("query") and f"Query: {a['query']}"]))
            targets.append({"target_id": aid[:512], "view_id": "adf.activity",
                            "args": {"pipeline": p["name"], "activity": a["activity"]}})

    for t in payload.get("triggers", []):
        tid = f"adf:trigger:{t['name']}"
        tsid = anchor("tr", tid)
        objects.append({"object_id": tid, "kind": "trigger", "label": t["name"][:512], "section_id": tsid,
                        "parent_object_id": None, "bindings": [], "dynamic": False, "opaque": False,
                        "coverage": coverage})
        sections.append(section(tsid, f"Trigger {t['name']}", [
            f"Type: {t.get('type')}", f"State: {t.get('state')}",
            "Starts: " + ", ".join(str(x) for x in t.get("startsPipelines") or [])]))
        targets.append({"target_id": tid, "view_id": "adf.trigger", "args": {"trigger": t["name"]}})

    for d in payload.get("dataflows", []):
        did = f"adf:dataflow:{d['name']}"
        dsid = anchor("df", did)
        objects.append({"object_id": did, "kind": "dataflow", "label": d["name"][:512], "section_id": dsid,
                        "parent_object_id": None, "bindings": [], "dynamic": False, "opaque": False,
                        "coverage": coverage})
        sections.append(section(dsid, f"Data flow {d['name']}", [
            d.get("description"), "Steps: " + ", ".join(s.get("name", "") for s in d.get("steps", []))]))
        targets.append({"target_id": did, "view_id": "adf.dataflow", "args": {"dataflow": d["name"]}})
    return objects, sections, targets
