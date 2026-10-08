"""Assemble the consolidated JSON and inject it into template.html."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from .column_usage import build_column_usage
from .object_index import build_search_index
from .power_query import power_query_view
from .table_kinds import defined_by
from .source_inventory import build_source_inventory
from .source_queries import build_source_queries
from .source_objects import build_source_objects
from .primary_sources import build_primary_sources
from .page_references import attach_report_locations, sync_page_usage, page_feed_rows
from .source_labels import source_label
from .quality import build_quality
from .live_connection import pairing as live_pairing, source_row as live_source_row

TEMPLATE = Path(__file__).parent / "template.html"


ENGINE_NAME = "pbi-doc-gen"


def producer(bidoc_version: str | None = None) -> dict:
    """What generated a payload: this engine and its version, and the bidoc generator that ran it when one did.

    `bidoc` is None when the engine was run on its own (pbi-doc-gen), where there is no generator version to record."""
    from . import __version__
    return {"engine": ENGINE_NAME, "engineVersion": __version__, "bidoc": bidoc_version or None}


def build_payload(model: dict | None, report: dict | None,
                  linked: dict | None, title: str) -> dict:
    mode = ("combined" if model and report
            else "semantic-only" if model
            else "report-only")
    if report:
        attach_report_locations(report)
    columns = build_column_usage(model, report) if model else None
    if model:
        sync_page_usage(model, report, linked, columns)
    if report:
        for page in report["pages"]:
            page["feeds"] = page_feed_rows({"columns": columns, "report": report}, page)
    source_objects = build_source_objects(model, report, columns)
    primary = build_primary_sources(model, report, source_objects, (columns or {}).get("globalIssues"),
                                    (columns or {}).get("tableIssues"))
    quality = build_quality(model)
    live = (report or {}).get("liveConnection")
    live_pair = live_pairing(live, model)
    if live_pair and live_pair["note"].startswith("Model supplied separately; its name"):
        report["warnings"].append({"severity": "warning", "category": "Model pairing", "message": live_pair["note"]})
    return {
        "extraction": (model or {}).get("extraction") or (report or {}).get("extraction"),
        "liveSource": live_source_row(live) if live else None,
        "livePairing": live_pair,
        "schemaVersion": 2,
        "title": title,
        "mode": mode,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "producer": producer(),
        "model": model,
        "report": report,
        "linked": linked,
        "columns": columns,
        "sourceObjects": source_objects,
        "primarySources": primary,
        "sourceQueries": build_source_queries(model, report),
        "tableSources": build_source_inventory(model, report, linked, columns) if model else [],
        "summary": build_summary(model, report, columns, primary, quality),
        "quality": quality,
    }


def build_summary(model, report, columns, primary, quality=None) -> dict:
    """Small, stable digest the documentation hub indexes across reports.

    The hub reads only this block (never the full payload) so its index does
    not break when views change. Values are counts and source identities; no
    code, SQL or credentials.
    """
    sources = {}
    for table in (model or {}).get("tables", []):
        for part in table.get("partitions", []):
            src = part.get("source") or {}
            if src.get("sourceType") in (None, "Unknown", "Calculated (DAX)"):
                continue
            label = src.get("label") or source_label(src)
            entry = sources.setdefault(label, dict(label=label, sourceType=src.get("sourceType") or "",
                                                   server=src.get("server") or "", database=src.get("database") or "",
                                                   location=src.get("detail") or "", tables=set()))
            entry["tables"].add(table["name"])
    live = (report or {}).get("liveConnection")
    if live:
        row = live_source_row(live)
        sources.setdefault(row["label"], dict(row, tables=set()))
    for row in (primary or {}).get("rows", []):
        if row.get("sourceType") in (None, "", "Unknown"):
            continue
        label = source_label(dict(row, detail=row.get("location")))
        entry = sources.setdefault(label, dict(label=label, sourceType=row["sourceType"], server=row.get("server") or "",
                                               database=row.get("database") or "", location=row.get("location") or "",
                                               tables=set()))
        entry["tables"].update(row.get("tables") or [])
    measure_counts = (columns or {}).get("measureCounts", {})
    column_counts = (columns or {}).get("counts", {})
    return {
        "counts": {
            "tables": len((model or {}).get("tables", [])),
            "columns": (columns or {}).get("columnCount", 0),
            "measures": len((model or {}).get("measures", [])),
            "pages": len((report or {}).get("pages", [])),
            "visuals": sum(len(p.get("visuals", [])) for p in (report or {}).get("pages", [])),
        },
        "sources": [dict(v, tables=sorted(v["tables"])) for _, v in sorted(sources.items())],
        "coverageIssues": len((columns or {}).get("issues", [])),
        "deletionCandidates": column_counts.get("Deletion candidate", 0) + measure_counts.get("Deletion candidate", 0),
        "needsReview": column_counts.get("Review", 0) + measure_counts.get("Review", 0),
        "measuresDescribed": ((quality or {}).get("documentation") or {}).get("measuresDescribed", 0),
        "duplicateMeasureSets": len((quality or {}).get("duplicateMeasures") or []),
    }


def build_derived(payload: dict) -> dict:
    """Content the page works out from the payload it is given: the search index and the Power Query view.

    It is rebuilt on every render from the payload being rendered, and is not part of the payload. A document
    re-rendered from a stored payload (a library import, a portable export) therefore gets it from the engine
    doing the rendering, and a shared document derives it only from what its projection left on the page.
    """
    return {"search": build_search_index(payload), "powerQuery": power_query_view(payload),
            "tableKinds": table_kinds_view(payload)}


def table_kinds_view(payload: dict) -> dict:
    """{table name: how it is defined} for a payload written before tables recorded it; empty otherwise."""
    tables = (payload.get("model") or {}).get("tables") or []
    return {t.get("name", ""): defined_by(t) for t in tables if isinstance(t, dict) and not isinstance(t.get("definedBy"), dict)}


# Every place the template is filled. One pass: text that has been inserted is never searched again, so a
# name in the payload that spells a slot is inert.
_SLOTS = re.compile(r"<!--__DOCUMENTATION_METADATA__-->|/\*__DOCUMENTATION_JS__\*/|__TITLE__|/\*__EXPLORER_CSS__\*/"
                    r"|/\*__EXPLORER_JS__\*/|/\*__DERIVED__\*/null|/\*__DATA__\*/null")
SCRIPTS = ("explorer.js", "model_kinds.js", "power_query.js", "navigation.js")
STYLES = ("explorer.css", "navigation.css", "power_query.css", "model_kinds.css")


def _part(name: str) -> str:
    return TEMPLATE.with_name(name).read_text(encoding="utf-8")


def render_html(payload: dict, out_path: str | Path) -> Path:
    from .catalog import read_metadata, validate_metadata, json_script
    out_path = Path(out_path)
    metadata = payload.get("documentation")
    if metadata is None and out_path.exists():
        metadata = read_metadata(out_path.read_text(encoding="utf-8-sig"))
    metadata = validate_metadata(metadata or {})
    payload = dict(payload, documentationFilename=out_path.name)
    slots = {
        "<!--__DOCUMENTATION_METADATA__-->":
            '<script type="application/json" id="pbi-documentation-metadata">' + json_script(metadata) + "</script>",
        "/*__DOCUMENTATION_JS__*/": _part("report_metadata.js"),
        "__TITLE__": payload["title"].replace("<", "&lt;"),
        "/*__EXPLORER_CSS__*/": "\n".join(_part(name) for name in STYLES),
        "/*__EXPLORER_JS__*/": "\n".join(_part(name) for name in SCRIPTS),
        "/*__DERIVED__*/null": json_script(build_derived(payload)),
        "/*__DATA__*/null": json_script(payload),
    }
    html = _SLOTS.sub(lambda match: slots[match.group(0)], TEMPLATE.read_text(encoding="utf-8"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
