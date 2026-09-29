"""Render the consolidated payload as an agent context document (markdown).

This supersedes the standalone bim_distill.py: a compact, front-loaded
markdown distillation of the solution, written for an LLM agent's context
window rather than a human's browser. Same analysis, same honesty rules as
every other renderer — one payload, four renderings (HTML / JSON / Word /
agent markdown).

Design rules for this format:
* Orientation first: what was supplied, what may be claimed, what's broken —
  before any detail, so a truncated read still orients correctly.
* Tables over prose; every fact scannable and greppable.
* Token-conscious: DAX truncated, M queries reduced to their source entity,
  no repetition. A rough token estimate is printed at the end of generation.
* Feasibility answered, not implied: the filter-reachability section states
  outright which tables can slice which, so "can I report X by Y?" needs no
  graph reasoning from the agent.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from .page_references import page_label

DAX_CHARS = 1200          # per-measure DAX budget in the agent doc


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _flat(s) -> str:
    """TMSL stores several text fields (description, expressions…) as either
    a string or a list of lines — normalise to one string."""
    if s is None:
        return ""
    if isinstance(s, (list, tuple)):
        return "\n".join(str(x) for x in s)
    return str(s)


def _cell(s) -> str:
    return (_flat(s)
            .replace("|", "\\|").replace("\n", " ").strip() or "—")


def _tbl(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_cell(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def _join(items, sep=", ", empty="—"):
    items = [str(i) for i in items if i]
    return sep.join(items) if items else empty


def _clip(s, n):
    s = re.sub(r"\s+", " ", _flat(s)).strip()
    return s if len(s) <= n else s[:n].rstrip() + " …[truncated]"


def estimate_tokens(text: str) -> int:
    return len(text) // 4


# ---------------------------------------------------------------------------
# derived analysis (pure functions of the payload)
# ---------------------------------------------------------------------------

def _reachability(model: dict) -> tuple[dict, dict]:
    """Filter propagation over ACTIVE relationships.

    Filters flow from the one side to the many side; both ways when the
    cross-filter is bidirectional. Returns (filters_downstream, sliceable_by):
    filters_downstream[T] = tables T's columns can filter;
    sliceable_by[T]       = tables with an active filter path to T.
    This does not infer the filter behaviour of measures homed on T.
    """
    graph: dict[str, set[str]] = {}
    for rel in model["relationships"]:
        if not rel["isActive"]:
            continue
        graph.setdefault(rel["toTable"], set()).add(rel["fromTable"])
        if rel["crossFilteringBehavior"] == "bothDirections":
            graph.setdefault(rel["fromTable"], set()).add(rel["toTable"])

    downstream: dict[str, list[str]] = {}
    for tbl in model["tables"]:
        start = tbl["name"]
        seen: set[str] = set()
        queue = deque(graph.get(start, ()))
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            queue.extend(graph.get(cur, ()))
        seen.discard(start)
        downstream[start] = sorted(seen)

    sliceable: dict[str, list[str]] = {t["name"]: [] for t in model["tables"]}
    for src, targets in downstream.items():
        for t in targets:
            sliceable.setdefault(t, []).append(src)
    return downstream, {k: sorted(v) for k, v in sliceable.items()}


def _partition_summary(tbl: dict) -> tuple[str, str]:
    """(storage modes, best source description) for a table."""
    modes = sorted({p.get("mode") or "import" for p in tbl["partitions"]}) or ["—"]
    src = ""
    for p in tbl["partitions"]:
        s = p.get("source") or {}
        obj = s.get("object") or s.get("detail") or ""
        bits = [b for b in (s.get("sourceType"), obj) if b]
        if s.get("server"):
            bits.append(f"@{s['server']}")
        if bits:
            src = " ".join(bits)
            break
    return "/".join(modes), src or "—"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def build_agent_md(payload: dict) -> str:
    model = payload.get("model")
    report = payload.get("report")
    linked = payload.get("linked")
    L: list[str] = []
    w = L.append

    # ---- orientation ----------------------------------------------------
    w(f"# Power BI solution: {payload['title']}")
    w("")
    honesty = [
        f"Agent context generated by pbi-doc-gen on {payload['generated']}. "
        f"Mode: **{payload['mode']}**.",
        "Everything below is design-time truth derived statically from the "
        "supplied files: no query history, no data values, credential and "
        "parameter *names* only.",
    ]
    if model and model.get("sourceFormat"):
        honesty.insert(1, f"Semantic model read from {model['sourceFormat']} "
                          f"(`{model.get('sourcePath') or '?'}`).")
    if payload["mode"] == "semantic-only":
        honesty.append("No report was supplied — nothing here can say whether "
                       "any object is actually *used*; usage claims are out of scope.")
    if payload["mode"] == "report-only":
        live = (report or {}).get("liveConnection")
        if live:
            honesty.append("The report has a live connection to "
                           + ", ".join(x for x in (live["kind"], live["server"], live["database"]) if x)
                           + "; its model and data sources live there and were not supplied.")
        honesty.append("No semantic model was supplied — field references are a "
                       "requirements manifest, not validated bindings.")
    if payload["mode"] == "combined":
        honesty.append('"No references" verdicts are scoped to this one report; '
                       "other reports on the same model are invisible.")
    w("> " + " ".join(honesty))
    w("")

    counts = []
    if model:
        counts += [f"{len(model['tables'])} tables",
                   f"{sum(len(t['columns']) for t in model['tables'])} columns",
                   f"{len(model['measures'])} measures",
                   f"{len(model['relationships'])} relationships",
                   f"{len(model['roles'])} RLS roles"]
    if report:
        counts += [f"{len(report['pages'])} pages",
                   f"{sum(len(p['visuals']) for p in report['pages'])} visuals",
                   f"{len(report['manifest'])} distinct field refs"]
    n_warn = sum(len(x["warnings"]) for x in (model, report, linked) if x)
    counts.append(f"{n_warn} warnings")
    w("Counts: " + " · ".join(counts) + ".")
    w("")

    # broken bindings surface before anything else — they change what an
    # agent may trust
    if linked:
        broken = [x for x in linked["warnings"] if x["category"] == "Broken binding"]
        if broken:
            w("## BROKEN BINDINGS (report references the model can't satisfy)")
            w("")
            for b in broken:
                w(f"- {b['message']}")
            w("")

    # ---- model ----------------------------------------------------------
    if model:
        usage_by_table = ({u["table"]: u for u in linked["tableUsage"]}
                          if linked else {})

        w("## Tables at a glance")
        w("")
        headers = ["Table", "Type", "Cols", "Measures", "Storage", "Source"]
        if linked:
            headers.append("Report usage")
        rows = []
        for t in model["tables"]:
            storage, src = _partition_summary(t)
            row = [t["name"], t["tableType"], len(t["columns"]),
                   len(t["measures"]), storage, src]
            if linked:
                row.append(usage_by_table.get(t["name"], {}).get("usage", "—"))
            rows.append(row)
        w(_tbl(headers, rows))

        # warnings, front-loaded
        all_warnings = model["warnings"] + (linked["warnings"] if linked else [])
        all_warnings = [x for x in all_warnings if x["category"] != "Broken binding"]
        if all_warnings:
            w("## Modelling caveats an agent must respect")
            w("")
            for x in all_warnings:
                w(f"- **{x['category']}** — {x['message']}")
            w("")

        w("## Relationships")
        w("")
        w("Filter direction: the **one** side filters the **many** side "
          "unless cross-filter is `both`. Inactive relationships apply only "
          "inside `USERELATIONSHIP()`.")
        w("")
        w(_tbl(["From (many)", "To (one)", "Cardinality", "Cross-filter", "Active"],
               [[f"{r['fromTable']}[{r['fromColumn']}]",
                 f"{r['toTable']}[{r['toColumn']}]",
                 f"{r['fromCardinality']}:{r['toCardinality']}",
                 "both" if r["crossFilteringBehavior"] == "bothDirections" else "single",
                 "yes" if r["isActive"] else "**NO**"]
                for r in model["relationships"]]))

        if model["relationships"]:
            w("```mermaid")
            w("erDiagram")
            for r in model["relationships"]:
                a = re.sub(r"\W+", "_", r["toTable"])
                b = re.sub(r"\W+", "_", r["fromTable"])
                label = r["toColumn"] + ("" if r["isActive"] else "_INACTIVE")
                w(f'    {a} ||--o{{ {b} : "{label}"')
            w("```")
            w("")

        downstream, sliceable = _reachability(model)
        w("## Filter reachability (model structure)")
        w("")
        w("This map follows active relationship directions between tables. A measure's "
          "home table is an organisational location, not a constraint on how it can be sliced. "
          "Assess the tables and columns read by its DAX and dependent measures. "
          "CALCULATE, REMOVEFILTERS, TREATAS and USERELATIONSHIP can change filter behaviour. "
          "Reachability alone does not establish whether a measure responds to a slicer; "
          "validate the expression and result in the intended filter context.")
        w("")
        w(_tbl(["Table", "Reachable from (active relationships)", "Filters (downstream)"],
               [[t["name"],
                 _join(sliceable.get(t["name"], []), empty="— nothing —"),
                 _join(downstream.get(t["name"], []), empty="— nothing —")]
                for t in model["tables"]]))

        w("## Columns")
        w("")
        for t in model["tables"]:
            if not t["columns"]:
                continue
            w(f"### {t['name']}")
            w("")
            rows = []
            for c in t["columns"]:
                notes = []
                if c["isCalculated"]:
                    notes.append("calc: `" + _clip(c["expression"] or "", 200) + "`")
                if c.get("dataCategory"):
                    notes.append(f"category={c['dataCategory']}")
                if c.get("sortByColumn"):
                    notes.append(f"sortBy={c['sortByColumn']}")
                if c["isHidden"]:
                    notes.append("hidden")
                if linked and c.get("reportUsage"):
                    notes.append(f"usage={c['reportUsage']}")
                if c.get("description"):
                    notes.append(_clip(c["description"], 160))
                rows.append([c["name"], c["dataType"], "; ".join(notes)])
            w(_tbl(["Column", "Type", "Notes"], rows))

        calc_groups = [t for t in model["tables"] if t.get("calculationGroup")]
        if calc_groups:
            w("## Calculation groups")
            w("")
            w("Calculation items rewrite whatever measure is in context, so they "
              "multiply the meaning of every measure below.")
            w("")
            for t in calc_groups:
                w(f"### {t['name']}")
                w("")
                for ci in t["calculationGroup"]:
                    w(f"- **{ci['name']}** — `{_clip(ci['expression'], 300)}`")
                w("")

        if model["measures"]:
            w("## Measures")
            w("")
            for m in model["measures"]:
                w(f"### [{m['name']}]  (on {m['table']})")
                meta = []
                if m.get("formatString"):
                    meta.append(f"format `{m['formatString']}`")
                if m.get("displayFolder"):
                    meta.append(f"folder `{m['displayFolder']}`")
                if m["isHidden"]:
                    meta.append("hidden")
                if linked:
                    meta.append("**used in report**" if m.get("usedInReport")
                                else "not used in this report")
                if meta:
                    w("_" + " · ".join(meta) + "_")
                if m.get("description"):
                    w(f"> {_clip(m['description'], 400)}")
                w("")
                w("```dax")
                w(_clip(m["expression"], DAX_CHARS))
                w("```")
                deps = []
                if m["dependsOnColumns"]:
                    deps.append("columns: " + _join(m["dependsOnColumns"]))
                if m["dependsOnMeasures"]:
                    deps.append("measures: " + _join(f"[{x}]" for x in m["dependsOnMeasures"]))
                if m["allTables"]:
                    deps.append("tables (transitive): " + _join(m["allTables"]))
                if deps:
                    w("Depends on — " + " | ".join(deps))
                if m.get("pageUsage"):
                    w("Used on — " + " | ".join(page_label(r) + ": " + r["usage"] for r in m["pageUsage"]))
                w("")

        if model["roles"]:
            w("## Row-level security")
            w("")
            for r in model["roles"]:
                w(f"### {r['name']} ({r.get('modelPermission') or 'read'})")
                for tp in r["tablePermissions"]:
                    w(f"- `{tp['table']}`: `{_clip(tp['filterExpression'], 300)}`")
                w("")

    # ---- report ---------------------------------------------------------
    if payload.get("tableSources"):
        w("## Table sources by report page")
        w("")
        w(_tbl(["Report", "Page", "Page ID", "Table", "Partition", "Page usage", "Server", "Database", "Object / query"],
               [[r["report"], r["page"] or r["pageScope"], r["pageId"], r["table"], r["partition"], r["usage"],
                 r["server"], r["database"], r["object"] or _clip(r["query"], 200)] for r in payload["tableSources"]]))
    if payload.get("columns"):
        w("## Column usage by report page")
        w("")
        w(payload["columns"]["scope"])
        w("")
        w(_tbl(["Report", "Page", "Page ID", "Column", "Page usage", "Assessment", "Measures on page"],
               [[r["report"], r["page"] or r["pageScope"], r["pageId"], f"{r['table']}[{r['column']}]",
                 r["pageUsage"], r["decision"], _join(r["pageMeasures"])] for r in payload["columns"]["rows"]]))
    if report:
        w("## Report structure")
        w("")
        if report.get("reportFilters"):
            w("Report-level filters: " +
              _join(f"{f.get('table') or ''}[{f.get('field')}]"
                    for f in report["reportFilters"]))
            w("")
        for p in report["pages"]:
            w(f"### Page: {p['label']}")
            if p.get("feeds"):
                w(_tbl(["Model table", "Fields on this page", "Page usage"],
                       [[r["table"], _join(r["fields"]), r["usage"]] for r in p["feeds"]]))
            page_filters = [f for f in report.get("filterRows", []) if f["pageId"] == p["id"]]
            if page_filters:
                w("Page filters: " +
                  _join(f"{f.get('table') or ''}[{f.get('field')}]"
                        for f in page_filters))
            w("")
            rows = []
            for v in p["visuals"]:
                fields = _join(f"{f.get('table') or ''}[{f.get('field')}]"
                               for f in v.get("fields", []))
                rows.append([v.get("type", "?"), v.get("title") or "—", fields])
            if rows:
                w(_tbl(["Visual", "Title", "Bound fields"], rows))

    # ---- lineage (combined) ---------------------------------------------
    if linked and linked.get("lineage"):
        w("## Lineage: source → table → report pages")
        w("")
        w(_tbl(["Report / page [ID]", "Source", "Object", "Table", "Page usage"],
               [[page_label(r), r["sourceType"], r["object"], r["table"], r["usage"]]
                for r in payload["tableSources"]]))

    return "\n".join(L)


def render_agent_md(payload: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_agent_md(payload), encoding="utf-8")
    return out_path
