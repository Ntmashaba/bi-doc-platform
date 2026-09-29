"""Combined-mode analysis: join the parsed model and the parsed report.

Produces, per table:
    usage = "direct"        bound in a visual, filter, or bookmark
          | "via measures"  reached only through DAX of measures the report uses
          | "possible"      reachable through an active relationship from a used table
          | "none"          nothing in the report references it, directly or indirectly

Also resolves every report field reference against the model (flagging broken
bindings) and assembles the source → table → measure → page lineage records.
"""

from __future__ import annotations
from .page_references import attach_report_locations


def _formatting_only(entry: dict) -> bool:
    locations = entry.get("locations") or []
    return bool(locations) and all((l.get("description") or "").startswith("Formatting rule") for l in locations)


def _broken(entry: dict, ref: str, reason: str) -> str:
    if _formatting_only(entry):
        # Only formatting uses it (a colour rule, a dynamic title): the visual
        # still shows data, but that formatting is broken.
        return f"A formatting rule refers to {ref}, but {reason}." + _used_on(entry)
    return f"Report references {ref} but {reason}." + _used_on(entry)


def _used_on(entry: dict, limit: int = 3) -> str:
    """" Used on: Page — Visual: x; ..." so a broken binding says where to fix it."""
    places = []
    for loc in entry.get("locations") or []:
        text = f"{loc.get('page') or 'No specific page'} — {loc.get('description') or loc.get('scope') or 'report'}"
        if text not in places:
            places.append(text)
    if not places:
        return ""
    more = f" (+{len(places) - limit} more)" if len(places) > limit else ""
    return " Used on: " + "; ".join(places[:limit]) + more + "."


def link(model: dict, report: dict) -> dict:
    attach_report_locations(report)
    table_names = {t["name"] for t in model["tables"]}
    columns = {(t["name"], c["name"]) for t in model["tables"] for c in t["columns"]}
    measures = {m["name"]: m for m in model["measures"]}
    # (table, hierarchy, level name) -> column: a level's name need not match its column.
    levels = {(t["name"], h["name"], lv.get("name")): lv.get("column")
              for t in model["tables"] for h in t.get("hierarchies", []) for lv in h.get("levelDetails", [])}
    warnings: list[dict] = []

    # ---- resolve every manifest entry ----------------------------------
    resolved_manifest = []
    used_tables_direct: set[str] = set()
    used_measures: set[str] = set()
    used_columns: set[tuple[str, str]] = set()

    for entry in report["manifest"]:
        table, field = entry["table"], entry["field"]
        kind = entry.get("kind")
        if kind == "hierarchyLevel" and levels.get((table, entry.get("hierarchy"), field)):
            field = levels[(table, entry["hierarchy"], field)]
        resolution, home = "unresolved", None
        # The report tells us whether it bound a column or a measure; trust it.
        # Only fall back to name lookup when it didn't (or the binding is stale),
        # otherwise a measure sharing a column's name steals the resolution.
        binds_column = kind in ("column", "hierarchyLevel")
        column_hit = table in table_names and (table, field) in columns
        if column_hit and (binds_column or field not in measures):
            resolution, home = "column", table
            used_columns.add((table, field))
            used_tables_direct.add(table)
        elif field in measures and not binds_column and (table is None or table in table_names):
            resolution, home = "measure", measures[field]["table"]
            used_measures.add(field)
            used_tables_direct.add(home)
        elif column_hit:
            resolution, home = "column", table
            used_columns.add((table, field))
            used_tables_direct.add(table)
        elif entry.get("runtime"):
            # Q&A visuals re-answer their question at runtime; a stale saved
            # answer is not a broken dependency.
            resolution, home = "runtime (Q&A)", table if table in table_names else None
        elif table in table_names:
            resolution, home = "missing field", table
            used_tables_direct.add(table)
            warnings.append({
                "severity": "warning", "category": "Broken binding",
                "message": _broken(entry, f"{table}[{field}]", "the model has no such column or measure"),
            })
        else:
            ref = f"{table}[{field}]" if table else f"[{field}]"
            reason = (f"table '{table}' does not exist in the model"
                      if table else "no measure with that name exists in the model")
            warnings.append({
                "severity": "warning", "category": "Broken binding",
                "message": _broken(entry, ref, reason),
            })
        resolved_manifest.append({**entry, "resolution": resolution, "homeTable": home})

    # ---- tables reached through used measures --------------------------
    via_measure_tables: set[str] = set()
    for name in used_measures:
        via_measure_tables |= set(measures[name]["allTables"])
    via_measure_tables -= used_tables_direct

    # ---- possible usage through active relationships -------------------
    adjacency: dict[str, set[str]] = {}
    for rel in model["relationships"]:
        if rel["isActive"]:
            adjacency.setdefault(rel["fromTable"], set()).add(rel["toTable"])
            adjacency.setdefault(rel["toTable"], set()).add(rel["fromTable"])

    reached = used_tables_direct | via_measure_tables
    possible: set[str] = set()
    for t in reached:
        possible |= adjacency.get(t, set())
    possible -= reached

    # ---- usage verdict + evidence per table ----------------------------
    field_evidence: dict[str, list] = {}
    for entry in resolved_manifest:
        home = entry["homeTable"]
        if home:
            field_evidence.setdefault(home, []).append(entry)

    table_usage = []
    for tbl in model["tables"]:
        name = tbl["name"]
        if name in used_tables_direct:
            verdict = "direct"
        elif name in via_measure_tables:
            verdict = "via measures"
        elif name in possible:
            verdict = "possible"
        else:
            verdict = "none"

        via = sorted(
            m for m in used_measures
            if name in measures[m]["allTables"] and measures[m]["table"] != name
        ) if verdict in ("direct", "via measures") else []

        neighbors = sorted(adjacency.get(name, set()) & reached) if verdict == "possible" else []

        pages = sorted({
            entry["usedIn"][i]
            for entry in field_evidence.get(name, [])
            for i in range(len(entry["usedIn"]))
        })

        table_usage.append({
            "table": name,
            "tableType": tbl["tableType"],
            "usage": verdict,
            "directFields": sorted({
                e["field"] for e in field_evidence.get(name, [])
                if e["resolution"] in ("column", "measure")
            }),
            "viaMeasures": via,
            "relatedUsedTables": neighbors,
            "whereUsed": pages,
        })

    # ---- column-level usage (combined verdict) -------------------------
    for tbl in model["tables"]:
        for col in tbl["columns"]:
            in_report = (tbl["name"], col["name"]) in used_columns
            in_used_measure = any(
                f"{tbl['name']}[{col['name']}]" in measures[m]["dependsOnColumns"]
                for m in used_measures
            )
            col["reportUsage"] = (
                "direct" if in_report
                else "via measures" if in_used_measure
                else "internal only" if col["internallyReferenced"]
                else "none"
            )

    for m in model["measures"]:
        m["usedInReport"] = m["name"] in used_measures
        m["usedBy"] = sorted({
            e["usedIn"][i]
            for e in resolved_manifest
            if e["resolution"] == "measure" and e["field"] == m["name"]
            for i in range(len(e["usedIn"]))
        })

    # ---- lineage records: source → table → consumers -------------------
    lineage = []
    for tbl in model["tables"]:
        usage = next(u for u in table_usage if u["table"] == tbl["name"])
        for part in tbl["partitions"]:
            src = part["source"]
            lineage.append({
                "sourceType": src["sourceType"],
                "sourceLabel": src.get("label") or src["sourceType"],
                "server": src.get("server"),
                "database": src.get("database"),
                "object": src.get("object") or src.get("detail"),
                "table": tbl["name"],
                "tableType": tbl["tableType"],
                "usage": usage["usage"],
                "consumers": usage["whereUsed"],
            })

    return {
        "manifest": resolved_manifest,
        "tableUsage": table_usage,
        "lineage": lineage,
        "warnings": warnings,
    }
