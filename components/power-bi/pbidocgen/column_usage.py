"""Column/page inventory with evidence, not an automatic deletion verdict.

The graph follows measures and calculated columns. Whole-table DAX references
are deliberately review blockers: COUNTROWS(T) does not read every column in T,
but static analysis cannot certify all table-expression semantics.
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from .page_references import page_ref
from .dax_lexer import mask_dax, REFERENCE as _REF

SCOPE = ("Scoped to the supplied model and report. Deletion candidates have no detected "
         "references; check other reports, Excel/composite-model consumers and Power Query "
         "steps before deleting. Static analysis is not proof of safe deletion.")
SOURCE_NOTE = ("Partition source is best effort. Source column is the model input name, "
               "not verified physical lineage through Power Query renames or transformations.")


def _names_table(text: str, table: str) -> bool:
    quoted = "'" + table.replace("'", "''") + "'"
    return quoted.casefold() in text.casefold() or bool(
        re.fullmatch(r"[^\W\d]\w*", table) and re.search(r"(?<![\w'])" + re.escape(table) + r"(?![\w'])", text, re.I))


_CUSTOM_VISUAL = re.compile(r"PBI_CV_|[0-9A-Fa-f]{32}$|\d{8,}$")


def _custom_visual(visual_type) -> bool:
    """Custom visual types carry a GUID or timestamp; their bindings are opaque."""
    return bool(visual_type and _CUSTOM_VISUAL.search(str(visual_type)))


_ROW_COUNT = re.compile(r"\b(?:COUNTROWS|ISEMPTY)\s*\(\s*(?:RELATEDTABLE\s*\(\s*)?"
                        r"(?:'(?:[^']|'')+'|[^\W\d]\w*)\s*\)?\s*\)", re.I)


def build_column_usage(model: dict, report: dict | None) -> dict:
    tables = {t["name"]: t for t in model["tables"]}
    table_lookup = {t.casefold(): t for t in tables}
    columns = {("c", t["name"], c["name"]): c for t in tables.values() for c in t["columns"]}
    col_lookup = {(t.casefold(), c.casefold()): key for key in columns for _, t, c in [key]}
    measures = {("m", m["table"], m["name"]): m for m in model["measures"]}
    mea_lookup = {key[2].casefold(): key for key in measures}
    by_column_name = defaultdict(list)
    for key in columns:
        by_column_name[key[2].casefold()].append(key)
    graph = defaultdict(set)
    table_deps = defaultdict(set)
    reasons = defaultdict(set)
    measure_reasons = defaultdict(set)  # non-measure model roots that need a measure
    uncertain = defaultdict(set)
    issues = set()  # global: blocks every deletion candidate
    table_issues = defaultdict(set)  # scoped: blocks only columns of the named table
    missing_table_issues = set()  # references to tables the model lacks: reported, block nothing

    def add_issue(message, table_name=""):
        """Scope an issue to a model table when the reference names one.

        An unresolved T[F] with a known T can only point at T, so it must not
        hold back unrelated tables. A T[F] whose T is not in the model cannot
        depend on any column that exists, so it is reported but blocks nothing.
        Bare references could mean anything and stay global.
        """
        home = table_lookup.get((table_name or "").casefold())
        if home:
            table_issues[home].add(message)
        elif table_name:
            missing_table_issues.add(message)
        else:
            issues.add(message)

    def label(key):
        return f"{key[1]}[{key[2]}]"

    def refs(expression, home, local_columns=False):
        text = mask_dax(expression or "")
        # Columns the expression creates itself: ADDCOLUMNS(T, "__x", ...) then [__x].
        local_names = {m.casefold() for m in re.findall(r'"((?:[^"]|"")+)"', expression or "")}
        found, spans = set(), []
        for match in _REF.finditer(text):
            field = match["field"].replace("]]", "]")
            table = (match["quoted"] or match["table"] or "").replace("''", "'")
            # DAX keywords before a bare reference (RETURN [Total]) are not
            # qualifiers. A quoted unknown table remains an unresolved ref.
            if match["table"] and table.upper() in {"RETURN", "NOT", "IN", "AND", "OR"} and table.casefold() not in table_lookup:
                table = ""
            key = None
            if table:
                key = col_lookup.get((table.casefold(), field.casefold()))
                candidate = mea_lookup.get(field.casefold())
                if not key and candidate and candidate[1].casefold() == table.casefold():
                    key = candidate
            else:
                if local_columns:
                    key = col_lookup.get((home.casefold(), field.casefold()))
                key = key or mea_lookup.get(field.casefold())
                if not key and field.casefold() in local_names:
                    spans.append(match.span())
                    continue  # a column defined inside this expression
                if not key:
                    # Row context: an unqualified [col] names a column of the
                    # table being iterated. Take the unique column with that
                    # name, or the one in a table this expression names.
                    candidates = by_column_name.get(field.casefold(), [])
                    if len(candidates) > 1:
                        candidates = [k for k in candidates if _names_table(text, k[1])] or candidates
                    if len(candidates) > 1 and home:
                        # DAX binds a bare [col] to the expression's own table
                        # when that table has the column (a measure on Calculations
                        # summing [Revenue] reads Calculations[Revenue]).
                        candidates = [k for k in candidates if k[1].casefold() == home.casefold()] or candidates
                    if len(candidates) == 1:
                        key = candidates[0]
                    elif candidates:
                        for k in candidates:
                            add_issue(f"Ambiguous DAX reference [{field}]", k[1])
                        spans.append(match.span())
                        continue
            if key:
                found.add(key)
            else:
                # Calculated columns, RLS filters and calculated tables have a
                # home table; an unresolved bare [col] there concerns that table.
                add_issue(f"Unresolved DAX reference {table}[{field}]", table or (home if local_columns else ""))
            spans.append(match.span())
        chars = list(text)
        for start, end in spans:
            chars[start:end] = " " * (end - start)
        remainder = "".join(chars)
        # COUNTROWS(T) / ISEMPTY(T) depend on T's rows, not its columns:
        # removing a column never changes the count.
        remainder = _ROW_COUNT.sub(" ", remainder)
        whole_tables = set()
        for table in tables:
            quoted = "'" + table.replace("'", "''") + "'"
            if re.search(re.escape(quoted), remainder, re.I) or (
                re.fullmatch(r"[^\W\d]\w*", table) and
                re.search(r"(?<![\w'])" + re.escape(table) + r"(?![\w']|\s*\()", remainder, re.I)
            ):
                whole_tables.add(table)
        return found, whole_tables

    for key, m in measures.items():
        expression = m["expression"] + "\n" + m.get("formatStringExpression", "")
        graph[key], table_deps[key] = refs(expression, key[1])
    for key, c in columns.items():
        if c.get("isCalculated"):
            graph[key], table_deps[key] = refs(c.get("expression"), key[1], True)

    # Output-to-input correspondence and runtime parameter choices are unknown.
    # Retain conservative edges labelled possible, never assert an exact path.
    possible_edges = set()
    for table, tbl in tables.items():
        for part in tbl["partitions"]:
            if part["type"] != "calculated":
                continue
            dependencies, whole = refs(part.get("expression"), table, True)
            dependencies |= {k for k in columns if k[1] in whole and k[1] != table}
            for output in (k for k in columns if k[1] == table):
                for dep in dependencies - {output}:
                    if dep not in graph[output]:
                        possible_edges.add((output, dep))
                    graph[output].add(dep)
                table_deps[output].update(whole)

    def closure(start):
        seen, touched = set(), set()
        todo = list(graph[start])
        touched.update(table_deps[start])
        while todo:
            key = todo.pop()
            if key in seen:
                continue
            seen.add(key)
            touched.update(table_deps[key])
            todo.extend(graph[key] - seen)
        return seen, touched

    closures = {key: closure(key) for key in list(graph)}
    used_by_measures = defaultdict(set)
    used_by_calculations = defaultdict(set)
    for root, (deps, whole_tables) in closures.items():
        what = ("Measure " if root[0] == "m" else "Calculated column ") + label(root)
        for dep in deps & columns.keys():
            reasons[dep].add(what)
            (used_by_measures if root[0] == "m" else used_by_calculations)[dep].add(label(root))
        if root[0] == "c":
            for dep in deps & measures.keys():
                measure_reasons[dep].add(what)
        for table in whole_tables:
            for key in columns:
                if key[1] == table:
                    uncertain[key].add(f"Whole-table dependency in {what}")

    def retain_expression(expression, home, what):
        deps, whole_tables = refs(expression, home, True)
        expanded = set(deps)
        for dep in deps:
            nested, nested_tables = closures.get(dep, (set(), set()))
            expanded.update(nested)
            whole_tables.update(nested_tables)
        for dep in expanded & columns.keys():
            reasons[dep].add(what)
            used_by_calculations[dep].add(what)
        for dep in expanded & measures.keys():
            measure_reasons[dep].add(what)
        for key in columns:
            if key[1] in whole_tables:
                uncertain[key].add(f"Whole-table dependency in {what}")

    for root in model.get("dependencyExpressions", []):
        retain_expression(root["expression"], root["table"], root["label"])
    for role in model["roles"]:
        for permission in role["tablePermissions"]:
            retain_expression(permission["filterExpression"], permission["table"],
                              "RLS role " + role["name"])
    for rel in model["relationships"]:
        for side in ("from", "to"):
            key = col_lookup.get((rel[side + "Table"].casefold(), rel[side + "Column"].casefold()))
            if key:
                reasons[key].add("Relationship " + rel["name"] + (" (inactive)" if not rel["isActive"] else ""))
    for table, tbl in tables.items():
        if tbl.get("calculationGroup"):
            for key in columns:
                if key[1] == table:
                    reasons[key].add("Calculation group structure")
        if any(p["type"] == "calculated" for p in tbl["partitions"]):
            for key in columns:
                if key[1] == table:
                    uncertain[key].add("Calculated table output: review its defining DAX before changing columns")
        for hierarchy in tbl["hierarchies"]:
            for column in hierarchy["levels"]:
                key = col_lookup.get((table.casefold(), (column or "").casefold()))
                if key:
                    reasons[key].add("Hierarchy " + hierarchy["name"])
        for c in tbl["columns"]:
            if c.get("sortByColumn"):
                sort = c["sortByColumn"].strip("'")
                key = col_lookup.get((table.casefold(), sort.casefold()))
                if key:
                    reasons[key].add("Sort-by for " + table + "[" + c["name"] + "]")
            if c.get("isKey"):
                reasons[("c", table, c["name"])].add("Model key column")

    # Key by stable page ID, never parse human labels (page names may contain /).
    page_use = defaultdict(lambda: defaultdict(lambda: {"kinds": set(), "evidence": set(), "measures": set()}))
    def usage_entry():
        return {"kinds": set(), "evidence": set(), "fields": set(), "measures": set()}
    table_page_use = defaultdict(lambda: defaultdict(usage_entry))
    measure_page_use = defaultdict(lambda: defaultdict(usage_entry))
    report_use = defaultdict(set)
    definite_report_use = set()
    report_measures = set()
    bookmark_use = defaultdict(set)
    pages = {p["id"]: p for p in (report or {}).get("pages", [])}

    def resolve(binding):
        table = table_lookup.get((binding.get("table") or "").casefold())
        field = (binding.get("field") or "").casefold()
        kind = binding.get("kind")
        if kind == "hierarchyLevel" and table:
            matches = set()
            for h in tables[table]["hierarchies"]:
                if binding.get("hierarchy") and h["name"] != binding["hierarchy"]:
                    continue
                for level in h.get("levelDetails", []):
                    if (level["name"] or "").casefold() == field:
                        key = col_lookup.get((table.casefold(), (level["column"] or "").casefold()))
                        if key:
                            matches.add(key)
            if len(matches) == 1:
                return matches.pop()
        if kind == "measure":
            key = mea_lookup.get(field)
            if key and (not binding.get("table") or key[1] == table):
                return key
        elif table:
            key = col_lookup.get((table.casefold(), field))
            if key:
                return key
        if field and not binding.get("runtime"):  # Q&A answers are re-derived at runtime
            add_issue(f"Unresolved report binding {binding.get('table') or '?'}[{binding.get('field')}]",
                      binding.get("table"))
        return None

    consumers = []
    node_id = lambda key: json.dumps(key, ensure_ascii=False, separators=(",", ":"))

    def consume(binding, page_ids, evidence, bookmark=False, visual_id=""):
        root = resolve(binding)
        if root is None:
            return
        deps, whole_tables = closures.get(root, (set(), set()))
        definite = {root}
        todo = [root]
        while todo:
            node = todo.pop()
            for dep in graph[node]:
                if dep not in definite and (node, dep) not in possible_edges:
                    definite.add(dep)
                    todo.append(dep)
        page_ids = list(page_ids)
        for pid in page_ids or [""]:
            consumers.append(dict(page_ref(report, pages.get(pid)), node=node_id(root),
                                  visualId=visual_id, evidence=evidence, bookmark=bookmark))
        for key in deps | {root}:
            kind = "Direct" if key == root else "Possible calculated-table dependency" if key not in definite else "Via measures" if root[0] == "m" else "Via calculations"
            for page_id in page_ids or [""]:
                trow = table_page_use[key[1]][page_id]
                trow["kinds"].add(kind)
                trow["evidence"].add(evidence)
                trow["fields"].add(key[2])
                if root[0] == "m":
                    trow["measures"].add(label(root))
                if key in measures:
                    mrow = measure_page_use[key][page_id]
                    mrow["kinds"].add(kind)
                    mrow["evidence"].add(evidence)
        for table in whole_tables:
            for page_id in page_ids or [""]:
                row = table_page_use[table][page_id]
                row["kinds"].add("Table expression")
                row["evidence"].add(evidence + ": " + label(root))
                if root[0] == "m":
                    row["measures"].add(label(root))
        report_measures.update(label(k) for k in deps | {root} if k in measures)
        affected = deps & columns.keys()
        if root in columns:
            affected = affected | {root}
        for key in affected:
            report_use[key].add(evidence)
            if key in definite:
                definite_report_use.add(key)
            else:
                uncertain[key].add("Possible calculated-table or field-parameter usage; runtime selection/output lineage is unresolved")
            if bookmark:
                bookmark_use[key].add(evidence)
            for page_id in page_ids:
                row = page_use[key][page_id]
                row["kinds"].add("Direct" if key == root else "Possible calculated-table dependency" if key not in definite else "Via measures" if root[0] == "m" else "Via calculations")
                row["evidence"].add(evidence)
                if root[0] == "m":
                    row["measures"].add(label(root))
                elif key not in definite:
                    row["measures"].update(label(m) for m in deps & measures.keys()
                                           if key in closures.get(m, (set(), set()))[0])
        for key in columns:
            if key[1] in whole_tables:
                uncertain[key].add(f"Report uses {label(root)} with a whole-table dependency ({evidence})")

    if report:
        for f in report["reportFilters"] + report.get("otherFields", []):
            consume(f, pages, "Report-level filter or expression")
        for page_id, page in pages.items():
            for f in page["filters"] + page.get("otherFields", []):
                consume(f, [page_id], f.get("level", "page").capitalize() + " filter or expression: " + page["name"])
            for v in page["visuals"]:
                for f in v["fields"]:
                    consume(f, [page_id], f"Visual {v['id']}: {v.get('title') or v['type']}", visual_id=v["id"])
        for bookmark in report["bookmarks"]:
            for f in bookmark["fields"]:
                # A bookmark may affect several pages; do not invent a page.
                consume(f, [], "Bookmark: " + bookmark["name"], bookmark=True)
        if not pages:
            issues.add("No report pages parsed")
        legacy = report.get("legacyLayout")
        # The legacy-layout caution is scoped below instead of blocking everything.
        issues.update(w["message"] for w in report["warnings"]
                      if w.get("severity") != "info"
                      and not (legacy and w.get("category") == "Legacy report layout"))
        if legacy:
            # Bookmark fields already count as used (Keep); only their page is
            # uncertain, which does not change a deletion decision.
            for page in pages.values():
                for v in page["visuals"]:
                    if v.get("unreadableBindings") or (_custom_visual(v.get("type")) and not v["fields"]
                                                        and v.get("unreadableBindings") is None):
                        issues.add(f"Legacy layout: bindings of custom visual '{v.get('title') or v['type']}'"
                                   f" on {page['name']} could not be read")
    else:
        issues.add("No report supplied; report usage is unknown")
    issues.update(w["message"] for w in model["warnings"] if w["category"] == "Unreadable definition")

    rows, counts = [], defaultdict(int)
    for key, col in sorted(columns.items()):
        _, table, column = key
        internal = sorted(reasons[key])
        review = sorted(uncertain[key] | table_issues[table] | issues)
        if report_use[key] or internal:
            decision = "Keep"
        elif review:
            decision = "Review"
        else:
            decision = "Deletion candidate"
        counts[decision] += 1
        if key in definite_report_use:
            counts["Used in report"] += 1
        elif report_use[key]:
            counts["Possible report usage"] += 1
        explanation = ("Referenced by the supplied report." if key in definite_report_use else
                       "Possible report usage through a calculated table or field parameter." if report_use[key] else
                       "Required by model dependencies." if internal else
                       "; ".join(review) if review else
                       "No report or model references detected. Validate other consumers and Power Query before removal.")
        sources = []
        for part in tables[table]["partitions"]:
            src = part["source"]
            sources.append(" / ".join(str(x) for x in [part["name"], src["sourceType"], src.get("server"),
                           src.get("database"), src.get("schema"), src.get("object") or src.get("detail")] if x))
        base = dict(table=table, column=column, decision=decision, reason=explanation,
                    report=report["name"] if report else "Not supplied",
                    usedInReport="Yes" if key in definite_report_use else "Possible" if report_use[key] else "Not detected" if report else "Unknown",
                    modelDependencies=internal, reviewNotes=review,
                    usedByMeasures=sorted(used_by_measures[key]),
                    usedByCalculations=sorted(used_by_calculations[key]),
                    bookmarkUsage=sorted(bookmark_use[key]), source=sources,
                    sourceColumn=col.get("sourceColumn") or "", sourceNote=SOURCE_NOTE,
                    expression=col.get("expression") or "", scope=SCOPE)
        for page_id in sorted(page_use[key]) or [None]:
            use = page_use[key].get(page_id)
            rows.append(dict(base, pageId=page_id or "", page=pages[page_id]["name"] if page_id else "",
                             pageScope="Page" if page_id else "Bookmark/report scope only" if report_use[key] else "No page usage detected" if report else "Not assessed",
                             pageUsage=" + ".join(sorted(use["kinds"])) if use else
                             "Bookmark/report scope only" if report_use[key] else
                             "Internal only" if internal else "Not assessed" if not report else "No references detected",
                             pageMeasures=sorted(use["measures"]) if use else [],
                             evidence=sorted(use["evidence"]) if use else sorted(report_use[key])))
    measure_rows, measure_counts = _assess_measures(
        measures, graph, measure_reasons, measure_page_use, report_measures,
        report, pages, issues, table_issues, label)
    table_assessments = _assess_tables(tables, rows, measure_rows, model["relationships"], report)

    # Track possible relationship dependencies separately on each real page.
    adjacency = defaultdict(set)
    for rel in model["relationships"]:
        if rel["isActive"]:
            adjacency[rel["fromTable"]].add(rel["toTable"])
            adjacency[rel["toTable"]].add(rel["fromTable"])
    for page_id in pages:
        seeds = {t for t, usage in table_page_use.items() if page_id in usage}
        reached, todo = set(seeds), list(seeds)
        while todo:
            for neighbor in adjacency[todo.pop()] - reached:
                reached.add(neighbor)
                todo.append(neighbor)
        for table in (reached - seeds) & tables.keys():
            row = table_page_use[table][page_id]
            row["kinds"].add("Possible relationship dependency")
            row["evidence"].add("Active relationship path to a table used on this page; verify filter propagation.")

    def flatten(table, page_id, use, **extra):
        ref = page_ref(report, pages.get(page_id))
        return dict(ref, table=table, usage=" + ".join(sorted(use["kinds"])) if page_id else "Bookmark/report scope only",
                    scope="Page" if page_id else "Bookmark/report scope only",
                    **{key: sorted(value) for key, value in use.items()}, **extra)
    table_pages = [flatten(table, pid, use) for table, uses in sorted(table_page_use.items()) for pid, use in sorted(uses.items())]
    for table in tables:
        if table not in table_page_use:
            table_pages.append(dict(page_ref(report), table=table,
                                    usage="No page usage detected" if report else "Usage unknown",
                                    scope="No page usage detected" if report else "Not assessed",
                                    kinds=[], evidence=[], fields=[], measures=[]))
    measure_pages = [flatten(key[1], pid, use, measure=key[2]) for key, uses in sorted(measure_page_use.items()) for pid, use in sorted(uses.items())]
    return {"rows": rows, "counts": dict(counts), "columnCount": len(columns), "csvFields": CSV_FIELDS,
            "measures": measure_rows, "measureCounts": measure_counts, "measureCsvFields": MEASURE_CSV_FIELDS,
            "tables": table_assessments,
            "tablePages": table_pages, "measurePages": measure_pages,
            "reportMeasures": sorted(report_measures),
            "dependencyGraph": {
                "nodes": [dict(id=node_id(k), kind="column" if k[0] == "c" else "measure",
                               table=k[1], name=k[2], label=label(k)) for k in sorted(columns.keys() | measures.keys())],
                "edges": [dict(dependent=node_id(k), dependency=node_id(d), possible=(k, d) in possible_edges)
                          for k, deps in sorted(graph.items()) for d in sorted(deps)],
                "wholeTableDependencies": [dict(node=node_id(k), table=t)
                                           for k, deps in sorted(table_deps.items()) for t in sorted(deps)],
                "consumers": consumers,
            },
            "scope": SCOPE, "sourceNote": SOURCE_NOTE,
            "issues": sorted(issues | missing_table_issues | {i for v in table_issues.values() for i in v}),
            "globalIssues": sorted(issues),
            "tableIssues": {t: sorted(v) for t, v in sorted(table_issues.items()) if v}}


def _assess_measures(measures, graph, measure_reasons, measure_page_use, report_measures,
                     report, pages, issues, table_issues, label):
    """Deletion assessment for measures, mirroring the column rules.

    A measure is kept when the report reaches it (directly, via another
    measure, or a bookmark) or when a non-measure model root needs it
    (calculated column, RLS, calculation item, detail rows). A measure used
    only by other unused measures is still a candidate; the note names them.
    """
    dependants = defaultdict(set)
    for key, deps in graph.items():
        for dep in deps:
            if dep in measures and key != dep:
                dependants[dep].add(key)
    rows, counts = [], defaultdict(int)
    for key, m in sorted(measures.items()):
        _, table, name = key
        in_report = label(key) in report_measures
        internal = sorted(measure_reasons[key])
        review = sorted(table_issues.get(table, set()) | issues)
        used_by = sorted(label(k) for k in dependants[key])
        if in_report or internal:
            decision = "Keep"
        elif review:
            decision = "Review"
        else:
            decision = "Deletion candidate"
        counts[decision] += 1
        page_ids = sorted(pid for pid in measure_page_use[key] if pid)
        if in_report:
            reason = "Used by the supplied report."
        elif internal:
            reason = "Required by model dependencies."
        elif review:
            reason = "; ".join(review)
        elif used_by:
            reason = ("Referenced only by measures or calculations that the report does not use (" +
                      ", ".join(used_by) + "). Remove them together or not at all.")
        else:
            reason = "No report or model references detected. Validate other reports and Excel consumers before removal."
        rows.append(dict(
            table=table, measure=name, decision=decision, reason=reason,
            report=report["name"] if report else "Not supplied",
            usedInReport="Yes" if in_report else "Not detected" if report else "Unknown",
            pages=[pages[pid]["name"] for pid in page_ids if pid in pages], pageIds=page_ids,
            usedBy=used_by, modelDependencies=internal, reviewNotes=review,
            displayFolder=m.get("displayFolder") or "", expression=m.get("expression") or "",
            scope=SCOPE))
    return rows, dict(counts)


def _assess_tables(tables, column_rows, measure_rows, relationships, report):
    """Whole tables whose every column and measure is a deletion candidate."""
    by_table = defaultdict(set)
    for r in column_rows:
        by_table[r["table"]].add(r["decision"])
    for r in measure_rows:
        by_table[r["table"]].add(r["decision"])
    related = {rel[side] for rel in relationships for side in ("fromTable", "toTable")}
    out = []
    for name, tbl in sorted(tables.items()):
        decisions = by_table.get(name, set())
        if not report or decisions != {"Deletion candidate"} or name in related or tbl.get("calculationGroup"):
            continue
        sources = sorted({p["source"].get("label") or p["source"].get("sourceType") or "Unknown"
                          for p in tbl["partitions"]})
        out.append(dict(table=name, decision="Deletion candidate",
                        reason="No column or measure in this table is used, and no relationship touches it.",
                        columns=sum(1 for r in column_rows if r["table"] == name and not r["pageId"]),
                        measures=sum(1 for r in measure_rows if r["table"] == name), sources=sources))
    return out


MEASURE_CSV_FIELDS = [("decision", "Deletion assessment"), ("usedInReport", "Used in report"),
                      ("report", "Report"), ("table", "Home table"), ("measure", "Measure"),
                      ("displayFolder", "Display folder"), ("pages", "Report pages"),
                      ("usedBy", "Used by measures/calculations"), ("modelDependencies", "Model dependencies"),
                      ("reason", "Assessment reason"), ("reviewNotes", "Review notes"),
                      ("expression", "DAX expression"), ("scope", "Assessment scope")]


CSV_FIELDS = [("decision", "Deletion assessment"), ("usedInReport", "Used in report"),
              ("report", "Report"), ("table", "Table"), ("column", "Column"), ("page", "Report page"), ("pageId", "Page ID"), ("pageScope", "Page scope"),
              ("pageUsage", "Page usage"), ("pageMeasures", "Measures on this page"),
              ("usedByMeasures", "Used by measures (all)"), ("usedByCalculations", "Used by calculations"),
              ("modelDependencies", "Model dependencies"), ("reason", "Assessment reason"),
              ("evidence", "Page evidence"), ("bookmarkUsage", "Bookmark usage"),
              ("source", "Partition sources"), ("sourceColumn", "Source column (model input)"),
              ("expression", "Column expression"), ("reviewNotes", "Review notes"),
              ("sourceNote", "Source limitation"), ("scope", "Assessment scope")]


def csv_cell(value):
    value = "; ".join(value) if isinstance(value, list) else str(value or "")
    # CSV quoting does not prevent Excel from evaluating a formula.
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        value = "'" + value
    return value


def write_column_csv(analysis: dict, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([title for _, title in CSV_FIELDS])
        for row in analysis["rows"]:
            writer.writerow([csv_cell(row[key]) for key, _ in CSV_FIELDS])
    return path
