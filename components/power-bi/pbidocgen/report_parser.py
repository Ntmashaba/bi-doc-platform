"""Parse a PBIR-format report folder (the enhanced *.Report folder produced
when Power BI Desktop saves a project with the PBIR preview format).

Expected layout (only what exists is read; everything is optional):

    MyReport.Report/
      definition/
        report.json                  <- report-level filters
        pages/
          pages.json                 <- page order / active page
          <PageId>/
            page.json                <- page name, visibility, page filters
            visuals/
              <VisualId>/
                visual.json          <- visual type, position, field bindings
        bookmarks/
          *.bookmark.json

Field bindings and filters are extracted by recursively walking the JSON for
the query-expression shapes PBIR uses:

    {"Column":  {"Expression": {"SourceRef": {"Entity"|"Source": ...}}, "Property": "..."}}
    {"Measure": {...same...}}
    {"HierarchyLevel"| "Aggregation": ... }

SourceRef.Source values are aliases declared in "from"/"Entities" lists; an
alias map is built per file and resolved before emitting references.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from .page_references import attach_report_locations
from .input_validation import validate_report


def _load(path: Path):
    """Read a PBIR JSON file. Returns None if it is absent or unparseable —
    every caller treats None as "this optional part is not present"."""
    for encoding in ('utf-8-sig', 'utf-16'):
        try:
            return validate_report(json.loads(path.read_text(encoding=encoding)))
        except (ValueError, UnicodeError, OSError, RecursionError):
            continue
    return None


# --------------------------------------------------------------------------
# Recursive extraction helpers
# --------------------------------------------------------------------------

_FIELD_KINDS = ("Column", "Measure", "HierarchyLevel", "Aggregation")


def _collect_aliases(node, aliases: dict):
    """Any {"Name": alias, "Entity": table} pair anywhere in the file."""
    if isinstance(node, dict):
        if "Name" in node and "Entity" in node and isinstance(node.get("Entity"), str):
            aliases[node["Name"]] = node["Entity"]
        elif (isinstance(node.get("Name"), str) and isinstance(node.get("Expression"), dict)
              and "Subquery" in node["Expression"]):
            # A subquery alias (q1): columns read through it are query outputs.
            aliases.setdefault(node["Name"], _SUBQUERY)
        for v in node.values():
            _collect_aliases(v, aliases)
    elif isinstance(node, list):
        for v in node:
            _collect_aliases(v, aliases)


# Alias bound to a subquery (From item with an Expression): its columns are
# query outputs, not model fields.
_SUBQUERY = "\0subquery"


def _scoped_aliases(node: dict, aliases: dict) -> dict:
    """A query's From list rebinds its aliases; the same letter can name a
    different table in a sibling query (o = Opportunities here, Owners there)."""
    items = node.get("From")
    if not isinstance(items, list):
        return aliases
    scoped = dict(aliases)
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("Name"), str):
            entity = item.get("Entity")
            scoped[item["Name"]] = entity if isinstance(entity, str) else _SUBQUERY
    return scoped


def _source_entity(expr, aliases: dict):
    """Resolve the table behind an Expression.SourceRef node."""
    if not isinstance(expr, dict):
        return None
    ref = expr.get("SourceRef") or expr.get("Expression", {}).get("SourceRef") \
        if isinstance(expr.get("Expression"), dict) else expr.get("SourceRef")
    if not isinstance(ref, dict):
        # walk one level deeper (Aggregation wraps Column, etc.)
        for v in expr.values():
            found = _source_entity(v, aliases) if isinstance(v, (dict, list)) else None
            if found:
                return found
        return None
    if "Entity" in ref:
        return ref["Entity"]
    if "Source" in ref:
        return aliases.get(ref["Source"], ref["Source"])
    return None


FORMATTING = "formatting"


def _collect_field_refs(node, aliases: dict, out: list, context: str = ""):
    """Find every Column/Measure/HierarchyLevel/Aggregation reference."""
    if isinstance(node, dict):
        aliases = _scoped_aliases(node, aliases)
        for kind in _FIELD_KINDS:
            if kind in node and isinstance(node[kind], dict):
                inner = node[kind]
                if kind == "Aggregation":
                    # unwrap to the underlying column, keep the function
                    func = inner.get("Function")
                    _collect_field_refs(inner.get("Expression"), aliases, out,
                                        context or f"aggregation({func})")
                    continue
                if kind == "HierarchyLevel":
                    level = inner.get("Level")
                    hierarchy = inner.get("Expression", {}).get("Hierarchy", {})
                    variation = (hierarchy.get("Expression") or {}).get("PropertyVariationSource") \
                        if isinstance(hierarchy, dict) and isinstance(hierarchy.get("Expression"), dict) else None
                    if isinstance(variation, dict) and isinstance(variation.get("Property"), str):
                        # Auto date/time hierarchy: the visual uses this date column
                        # through its hidden date table, not a model hierarchy.
                        table = _source_entity(variation, aliases)
                        if table and table != _SUBQUERY:
                            out.append({"table": table, "field": variation["Property"],
                                         "kind": "column", "context": context})
                        continue
                    table = _source_entity(hierarchy.get("Expression", {}), aliases) \
                        or _source_entity(hierarchy, aliases)
                    if table and level and table != _SUBQUERY:
                        out.append({"table": table, "field": level,
                                    "hierarchy": hierarchy.get("Hierarchy"),
                                    "kind": "hierarchyLevel", "context": context})
                    continue
                prop = inner.get("Property")
                expression = inner.get("Expression") or {}
                if isinstance(expression, dict) and "Subquery" in expression:
                    # A column of an inline subquery's output, not a model field.
                    # The subquery's own inputs are real references.
                    _collect_field_refs(expression, aliases, out, context)
                    continue
                if isinstance(expression, dict) and "TransformTableRef" in expression:
                    # Output of an analytics transform (forecast, anomaly
                    # detection): a computed column, not a model field. The
                    # transform's real inputs are collected from its own query.
                    continue
                table = _source_entity(expression, aliases)
                if table == _SUBQUERY:
                    continue
                if not table and isinstance(prop, str) and "." in prop:
                    # A "Table.Field" name with no SourceRef is a query label (text-box
                    # dynamic values); the prefix need not be the home table. The real
                    # field is collected from the visual's query.
                    continue
                if prop:
                    out.append({
                        "table": table,
                        "field": prop,
                        "kind": "column" if kind == "Column" else "measure",
                        "context": context,
                    })
        for key, v in node.items():
            if key in _FIELD_KINDS:
                continue
            # objects / vcObjects hold formatting: conditional colours, titles,
            # data labels. A field there drives formatting, not the data shown.
            _collect_field_refs(v, aliases, out, FORMATTING if key in ("objects", "vcObjects") else context)
    elif isinstance(node, list):
        for v in node:
            _collect_field_refs(v, aliases, out, context)


_FILTER_TYPES = {
    "Categorical": "basic", "Advanced": "advanced", "TopN": "top N",
    "RelativeDate": "relative date", "RelativeTime": "relative time",
    "Passthrough": "passthrough", "Tuple": "tuple",
}


def _collect_filters(config, aliases: dict, level: str, target: str) -> list[dict]:
    """Pull the filters array out of a filterConfig / filters blob."""
    out = []
    if config is None:
        return out
    if isinstance(config, str):
        parsed = None
        try:
            parsed = json.loads(config)
        except json.JSONDecodeError:
            return out
        config = parsed
    filters = config.get("filters", config) if isinstance(config, dict) else config
    if not isinstance(filters, list):
        return out
    for f in filters:
        if not isinstance(f, dict):
            continue
        refs: list[dict] = []
        _collect_field_refs(f.get("field") or f.get("expression") or f, aliases, refs)
        first = refs[0] if refs else {}
        out.append({
            "level": level,
            "target": target,
            "table": first.get("table"),
            "field": first.get("field"),
            "kind": first.get("kind", "column"),
            "filterType": _FILTER_TYPES.get(f.get("type", ""), (f.get("type") or "basic")),
            "name": f.get("name"),
            "isHidden": bool((f.get("howCreated") == 1) or f.get("isHiddenInViewMode", False)),
            "raw": _summarize_condition(f),
        })
    return out


def _summarize_condition(f: dict) -> str | None:
    """A short human-readable hint of the filter condition if present."""
    blob = f.get("filter") or f.get("Where") or f.get("condition")
    if blob is None:
        return None
    text = json.dumps(blob)
    literals = re.findall(r"'([^']*)'|\"Value\"\s*:\s*\"([^\"]*)\"", text)
    vals = [a or b for a, b in literals if (a or b)]
    vals = [v.strip("'") for v in vals if not v.startswith("{")]
    if vals:
        uniq = list(dict.fromkeys(vals))[:6]
        return ", ".join(uniq)
    return None


# --------------------------------------------------------------------------
# Main parse
# --------------------------------------------------------------------------

def parse_report(report_path: str | Path) -> dict:
    from .custom_visuals import from_folder as custom_visual_names
    root = Path(report_path)
    from .extracted_report import is_legacy_layout, parse_legacy_layout  # avoids an import cycle
    if is_legacy_layout(root):
        # PBIP saved before PBIR: one report.json with every page and visual.
        report = parse_legacy_layout(root, root.name.replace(".Report", ""))
        report["customVisuals"] = custom_visual_names(root)
        return report
    definition = root / "definition"
    if not definition.exists():
        # tolerate being handed the definition folder itself
        if (root / "pages").exists() or (root / "report.json").exists():
            definition = root
        else:
            raise FileNotFoundError(
                f"'{root}' does not look like a PBIR report folder "
                f"(no definition/ subfolder found)."
            )

    warnings: list[dict] = []
    pages_out: list[dict] = []
    report_filters: list[dict] = []
    bookmarks_out: list[dict] = []

    # ---- report-level filters ------------------------------------------
    report_json = _load(definition / "report.json") or {}
    if not (definition / "pages").is_dir():
        warnings.append({"severity": "warning", "category": "Incomplete report",
                         "message": "PBIR pages are missing/unreadable; deletion candidates cannot be assessed."})
    elif not report_json:
        # report.json holds report-level filters and settings only. Without it
        # there are no report-level filters to miss, so pages still decide usage.
        warnings.append({"severity": "info", "category": "Report settings missing",
                         "message": "definition/report.json is missing or unreadable; report-level filters and "
                                    "settings are not documented. Page and visual usage is unaffected."})
    aliases: dict = {}
    _collect_aliases(report_json, aliases)
    report_fields = []
    _collect_field_refs(report_json, aliases, report_fields)
    report_filters = _collect_filters(
        report_json.get("filterConfig"), aliases, "report", "(entire report)"
    )

    # ---- pages ----------------------------------------------------------
    pages_dir = definition / "pages"
    order: list[str] = []
    active = None
    pages_meta = _load(pages_dir / "pages.json") or {}
    order = pages_meta.get("pageOrder", [])
    if not isinstance(order, list) or not all(isinstance(pid, str) for pid in order):
        warnings.append({"severity": "warning", "category": "Incomplete report", "message": "Invalid pages.json pageOrder; page coverage is uncertain."})
        order = []
    if (pages_dir / "pages.json").exists() and not pages_meta:
        warnings.append({"severity": "warning", "category": "Incomplete report", "message": "Unreadable pages.json; page coverage is uncertain."})
    for pid in order:
        if pid not in {d.name for d in pages_dir.iterdir() if d.is_dir()}:
            warnings.append({"severity": "warning", "category": "Incomplete report", "message": f"Declared page {pid!r} is missing from the extract."})
    active = pages_meta.get("activePageName")

    page_dirs = [d for d in pages_dir.iterdir() if d.is_dir()] if pages_dir.exists() else []

    def sort_key(d: Path):
        return order.index(d.name) if d.name in order else len(order)

    for page_dir in sorted(page_dirs, key=sort_key):
        page_json = _load(page_dir / "page.json")
        if page_json is None:
            warnings.append({"severity": "warning", "category": "Unreadable page",
                             "message": f"Could not parse {page_dir.name}/page.json; page skipped."})
            continue
        p_aliases: dict = {}
        _collect_aliases(page_json, p_aliases)
        page_fields = []
        _collect_field_refs(page_json, p_aliases, page_fields)
        display = page_json.get("displayName") or page_json.get("name") or page_dir.name
        visibility = page_json.get("visibility")
        hidden = visibility in ("HiddenInViewMode", "hidden", 1)

        page_filters = _collect_filters(page_json.get("filterConfig"),
                                        p_aliases, "page", display)

        visuals_out: list[dict] = []
        visuals_dir = page_dir / "visuals"
        if visuals_dir.exists():
            for vis_dir in sorted(d for d in visuals_dir.iterdir() if d.is_dir()):
                vis_json = _load(vis_dir / "visual.json")
                if vis_json is None:
                    warnings.append({
                        "severity": "warning", "category": "Unreadable visual",
                        "message": f"Could not parse visual {vis_dir.name} on page '{display}'.",
                    })
                    continue
                v_aliases: dict = {}
                _collect_aliases(vis_json, v_aliases)
                visual_node = vis_json.get("visual", {})
                vtype = visual_node.get("visualType", "unknown")
                pos = vis_json.get("position", {})

                # title: objects.title[...] properties.text literal, best effort
                title = None
                text = json.dumps(visual_node.get("objects", {}).get("title", []))
                m = re.search(r"'([^']+)'", text)
                if m:
                    title = m.group(1)

                # field refs: roles under query/queryState carry role names
                refs: list[dict] = []
                query_state = (visual_node.get("query", {}) or {}).get("queryState", {})
                if query_state:
                    for role, blob in query_state.items():
                        _collect_field_refs(blob, v_aliases, refs, context=role)
                else:
                    _collect_field_refs(visual_node.get("query", visual_node),
                                        v_aliases, refs)

                # Include conditional formatting, dynamic titles and other
                # expressions outside queryState in the usage inventory.
                extra_refs = []
                _collect_field_refs(vis_json, v_aliases, extra_refs, context="visual expression")
                bound = {(r["table"], r["field"], r["kind"], r.get("hierarchy")) for r in refs}
                refs.extend(r for r in extra_refs
                            if (r["table"], r["field"], r["kind"], r.get("hierarchy")) not in bound)

                # de-duplicate identical refs
                seen = set()
                fields = []
                for r in refs:
                    key = (r["table"], r["field"], r["kind"], r.get("hierarchy"), r["context"])
                    if key in seen:
                        continue
                    seen.add(key)
                    fields.append(r)

                visual_filters = _collect_filters(vis_json.get("filterConfig"),
                                                  v_aliases, "visual",
                                                  f"{display} / {title or vtype}")
                page_filters.extend(visual_filters)

                if vtype == "qnaVisual":
                    for r in fields:
                        r["runtime"] = True
                visuals_out.append({
                    "id": vis_dir.name,
                    "type": vtype,
                    "title": title,
                    "x": pos.get("x"), "y": pos.get("y"),
                    "width": pos.get("width"), "height": pos.get("height"),
                    "hidden": bool(vis_json.get("isHidden", False)),
                    "fields": fields,
                    "filters": visual_filters,
                })

        pages_out.append({
            "id": page_dir.name,
            "name": display,
            "hidden": hidden,
            "isActive": page_dir.name == active,
            "width": page_json.get("width"), "height": page_json.get("height"),
            "visuals": visuals_out,
            "filters": page_filters,
            "otherFields": page_fields,
        })

    # ---- bookmarks ------------------------------------------------------
    bookmarks_dir = definition / "bookmarks"
    if bookmarks_dir.exists():
        for bm_file in sorted(bookmarks_dir.glob("*.json")):
            bm = _load(bm_file)
            if not isinstance(bm, dict):
                warnings.append({"severity": "warning", "category": "Unreadable bookmark",
                                 "message": f"Could not parse {bm_file.name}."})
                continue
            b_aliases: dict = {}
            _collect_aliases(bm, b_aliases)
            refs: list[dict] = []
            _collect_field_refs(bm, b_aliases, refs, context="bookmark")
            bookmarks_out.append({
                "name": bm.get("displayName") or bm_file.stem,
                "fields": refs,
            })

    from .live_connection import from_pbir, summary as live_summary
    live = from_pbir(root.parent if root.name == "definition" else root)
    if live:
        warnings.append({"severity": "warning", "category": "External semantic model",
                         "message": "This report is live-connected (" + live_summary(live) + "); tables, measures "
                                    "and data sources live in that model. Supply the model to document them."})
    return attach_report_locations({
        "liveConnection": live,
        "name": (root.parent.name if root.name == "definition" else root.name).replace(".Report", ""),
        "pages": pages_out,
        "reportFilters": report_filters,
        "otherFields": report_fields,
        "bookmarks": bookmarks_out,
        "manifest": [],
        "warnings": warnings,
        "customVisuals": custom_visual_names(root.parent if root.name == "definition" else root),
    })
