"""Power BI adapter over pbi-doc-gen (identity `pbi-identity/1`)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pbidocgen
from bidoc_contracts import power_bi_scope
from pbidocgen.linker import link
from pbidocgen.model_parser import parse_model
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.report_parser import parse_report

from . import endpoints
from .envelope import anchor, section

DOCUMENT_TYPE = "power_bi"
ENGINE = "pbi-doc-gen"
ENGINE_VERSION = pbidocgen.__version__
IDENTITY_VERSION = "pbi-identity/1"
SOURCE_KINDS = ("pbip", "tmdl", "bim", "pbir", "extracted")
VIEW_IDS = frozenset({"pbi.overview", "pbi.table", "pbi.measure", "pbi.source", "pbi.page"})


class InputError(ValueError):
    pass


def native_schema(payload) -> str:
    return f"{ENGINE}/{payload.get('schemaVersion')}"


def load(source: Path, kind: str, title: str | None = None) -> dict:
    """Run the engine on a PBIP project, a model (TMDL folder or model.bim) or a PBIR report."""
    source = Path(source)
    if not source.exists():
        raise InputError(f"input not found: {source}")
    model = report = None
    project_title = None
    try:
        if kind == "pbip":
            from pbidocgen.project import discover
            found = discover(str(source))
            project_title = found["title"]
            model = parse_model(found["model"]) if found["model"] else None
            report = parse_report(found["report"]) if found["report"] else None
            if not model and not report:
                raise InputError(f"nothing to document in {source}")
        elif kind in ("tmdl", "bim"):
            model = parse_model(source)
        elif kind == "pbir":
            report = parse_report(source)
        elif kind == "extracted":
            # A pbi-tools extract folder (Model/, Report/). The engine assembles files beside
            # the extract, so it reads a temporary copy and the source is left untouched.
            from pbidocgen.pbix_batch import load_extracted
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / source.name
                shutil.copytree(source, work)
                model, report = load_extracted(work, work.with_suffix(".pbix"), True)
            project_title = source.name
        else:
            raise InputError(f"unsupported Power BI input kind {kind!r}; supported: {', '.join(SOURCE_KINDS)}")
    except (FileNotFoundError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(str(exc)) from exc
    linked = link(model, report) if model and report else None
    name = title or (model["name"] if model else None) or project_title or (report["name"] if report else "Power BI")
    return build_payload(model, report, linked, name)


def scope(payload):
    """(descriptor, complete, coverage warnings). A parsed project/model/report is a complete snapshot."""
    warnings = [w.get("message", "") for w in (payload.get("model") or {}).get("warnings", [])
                if w.get("message")][:50]
    return power_bi_scope(payload["mode"]), True, warnings


def render(payload) -> str:
    with tempfile.TemporaryDirectory() as d:
        out = render_html(payload, Path(d) / "document.html")
        return out.read_text(encoding="utf-8")


def _enc(value) -> str:
    return str(value or "").replace("%", "%25").replace(":", "%3A")


def _source_endpoint(s: dict) -> dict:
    kind = s.get("sourceKind")
    location = s.get("location") or ""
    if kind == "file" or str(s.get("server") or "").startswith(("http://", "https://", "\\\\")):
        url = location if location.startswith(("http://", "https://")) else None
        return endpoints.endpoint(system=s.get("sourceType"), url=url, path=None if url else location or None,
                                  storage_account=endpoints.storage_account_of(url), object=s.get("object"))
    host, instance, port = endpoints.split_server(s.get("server"))
    return endpoints.endpoint(system=s.get("sourceType"), server=host, instance=instance, port=port,
                              database=s.get("database"), schema=s.get("schema"), object=s.get("object"))


def _source_id(ep: dict) -> str:
    parts = [ep["system"], ep["server"], ep["instance"], ep["port"], ep["database"], ep["schema"],
             ep["object"], ep["url"], ep["path"]]
    return "pbi:source:" + ":".join(_enc(p) for p in parts)


def describe(payload: dict, coverage: str = "complete"):
    """objects, sections, navigation targets from a (projected) payload."""
    objects, sections, targets = [], [], []
    model = payload.get("model") or {}
    report = payload.get("report") or {}
    overview = "pbidoc-overview"
    sections.append(section(overview, payload.get("title"), [
        f"Power BI documentation ({payload.get('mode')}).",
        f"{len(model.get('tables', []))} tables, {len(model.get('measures', []))} measures, "
        f"{len(report.get('pages', []))} report pages." if model or report else ""]))
    targets.append({"target_id": overview, "view_id": "pbi.overview", "args": {}})

    for t in model.get("tables", []):
        tid = f"pbi:table:{t['lineageTag']}" if t.get("lineageTag") else f"pbi:table:name:{t['name']}"
        sid = anchor("t", tid)
        objects.append({"object_id": tid, "kind": "table", "label": t["name"][:512], "section_id": sid,
                        "parent_object_id": None, "bindings": [], "dynamic": False, "opaque": False,
                        "coverage": coverage})
        sections.append(section(sid, f"Table {t['name']}", [
            t.get("description"),
            "Columns: " + ", ".join(c["name"] for c in t.get("columns", [])) if t.get("columns") else "",
            "Measures: " + ", ".join(m["name"] for m in t.get("measures", [])) if t.get("measures") else "",
            "Sources: " + ", ".join(sorted({p["source"].get("label") or "" for p in t.get("partitions", [])
                                              if isinstance(p.get("source"), dict)} - {""})),
        ]))
        targets.append({"target_id": tid, "view_id": "pbi.table", "args": {"table": t["name"]}})
        for m in t.get("measures", []):
            mid = (f"pbi:measure:{m['lineageTag']}" if m.get("lineageTag")
                   else f"pbi:measure:name:{t['name']}/{m['name']}")
            msid = anchor("m", mid)
            objects.append({"object_id": mid, "kind": "measure", "label": m["name"][:512], "section_id": msid,
                            "parent_object_id": tid, "bindings": [], "dynamic": False, "opaque": False,
                            "coverage": coverage})
            sections.append(section(msid, f"Measure {m['name']}", [
                f"Table: {t['name']}", m.get("description"), m.get("displayFolder") and f"Folder: {m['displayFolder']}",
                m.get("expression") and f"{m['name']} = {m['expression']}"]))
            targets.append({"target_id": mid, "view_id": "pbi.measure", "args": {"table": t["name"], "measure": m["name"]}})

    # sourceObjects has one row per (logical source, page usage); objects are the logical sources.
    grouped: dict[str, dict] = {}
    for i, s in enumerate(payload.get("sourceObjects") or []):
        if not isinstance(s, dict) or not (s.get("server") or s.get("object") or s.get("location")):
            continue
        ep = _source_endpoint(s)
        g = grouped.setdefault(_source_id(ep), {"ep": ep, "rows": [], "tables": set(), "row": s})
        g["rows"].append(i)
        if s.get("table"):
            g["tables"].add(s["table"])
    for sid_obj, g in grouped.items():
        s, ep = g["row"], g["ep"]
        resolved = s.get("status") == "Resolved"
        label = " ".join(x for x in (s.get("database"), ".".join(y for y in (s.get("schema"), s.get("object")) if y))
                         if x) or s.get("object") or s.get("location") or s.get("sourceType") or "source"
        ssid = anchor("s", sid_obj)
        objects.append({"object_id": sid_obj[:512], "kind": "source", "label": label[:512], "section_id": ssid,
                        "parent_object_id": None, "dynamic": not resolved, "opaque": False, "coverage": coverage,
                        "bindings": [{"binding_id": f"{sid_obj}#read"[:512], "operation": "read", "endpoint": ep,
                                      "normalized_endpoint": endpoints.normalize(ep),
                                      "normalization_version": endpoints.NORMALIZATION_VERSION,
                                      "invocation_context": {"tables": ", ".join(sorted(g["tables"]))[:500]},
                                      "evidence_refs": [f"/sourceObjects/{g['rows'][0]}"],
                                      "resolution": "static" if resolved else "partial", "coverage": coverage}]})
        sections.append(section(ssid, f"Source {label}"[:200], [
            f"Type: {s.get('sourceType')}", s.get("server") and f"Server: {s['server']}",
            s.get("database") and f"Database: {s['database']}", s.get("object") and f"Object: {s['object']}",
            "Used by tables: " + ", ".join(sorted(g["tables"])) if g["tables"] else "", f"Status: {s.get('status')}"]))
        targets.append({"target_id": sid_obj[:512], "view_id": "pbi.source", "args": {"source": label[:200]}})

    for p in report.get("pages", []):
        psid = anchor("p", f"page:{p.get('id') or p.get('name')}")
        sections.append(section(psid, f"Page {p.get('name')}", [
            "Visuals: " + ", ".join(v.get("title") or v.get("type") or "" for v in p.get("visuals", []))]))
        targets.append({"target_id": psid, "view_id": "pbi.page", "args": {"page": str(p.get("id") or p.get("name"))}})
    return objects, sections, targets
