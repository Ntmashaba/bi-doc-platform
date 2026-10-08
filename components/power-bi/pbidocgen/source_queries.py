"""The Power Query inventory: one row per query in the file, whatever the file format.

The inventory covers the complete supplied model, independently of report-page usage: shared functions,
parameters and staging queries may feed many tables. The first three keys of a row (`report`, `queryName`,
`mCode`) are the three-column CSV export and are unchanged; the rest describes the query for the Power Query
view. Nothing in a row besides `mCode` holds query code, so a shared document that withholds code still says
which queries exist, what they feed and how completely they were read.

Where one query is supplied more than once, this order decides which representation is listed:

1. A table's partition expression is the query of record for that table: it is what a refresh runs.
2. A shared expression with the same name and the same text as a table's query is that same query, listed once
   as the table's query. (A pbi-tools extract can supply one `queries/<name>.m` to both.)
3. A shared expression with the same name but different text is a different query. Both are listed, each
   saying where it comes from; neither is dropped or merged.
4. Partitions of one table with identical text are one query, used by each of those partitions. Partitions with
   different text are one query each, named `Table / Partition`.
5. In a pre-2019 file the queries live in a Power Query package. The member a table loads is that table's
   query (rule 1); every other member is a shared query. The placeholder SQL the model stores for the table
   (`SELECT * FROM [Name]`) is never listed as a query.
6. A structured data source is a data source, not a query.
"""
from __future__ import annotations

from . import m_steps
from .m_sources import INTERNAL_SOURCES, Tracer, materialize
from .object_index import query_id
from .source_labels import source_label

LOAD = ("loaded", "not loaded", "unknown")


def _text(code) -> str:
    return (code or "").replace("\r\n", "\n").strip()


def _table_queries(table: dict):
    """[(name, code, [partitions])] for the Power Query queries a table's partitions hold (rule 4)."""
    partitions = table.get("partitions") or []
    powerquery = [p for p in partitions if (p.get("type") or "").lower() == "m" or p.get("mashupLocation")]
    by_text: dict[str, list] = {}
    for part in powerquery:
        by_text.setdefault(_text(part.get("expression") if (part.get("type") or "").lower() == "m" else ""), []).append(part)
    out = []
    for parts in by_text.values():
        if len(partitions) == 1 or (len(by_text) == 1 and len(powerquery) == len(partitions) and len(parts) > 1):
            name = table["name"]
        else:
            name = f"{table['name']} / {parts[0].get('name', '')}"
        code = parts[0].get("expression") or "" if (parts[0].get("type") or "").lower() == "m" else ""
        out.append((name, code, parts))
    return out


def build_source_queries(model: dict | None, report: dict | None) -> list[dict]:
    if not model:
        return []
    from .source_objects import source_definitions      # avoid an import cycle
    report_name = (report or {}).get("name") or model.get("name") or "Not supplied"
    recorded_order = {name: i for i, name in enumerate(model.get("queryOrder") or []) if isinstance(name, str)}
    rows: list[dict] = []

    def add(name, code, origin, **facts):
        row = {"report": report_name, "queryName": name, "mCode": code or "", "origin": origin}
        row.update(facts)
        row["_file"] = len(rows)
        rows.append(row)
        return row

    unresolved_legacy = 0
    table_rows: dict[str, dict] = {}                      # table name -> its query, when it has exactly one
    for table in model.get("tables", []):
        found = _table_queries(table)
        for name, code, parts in found:
            legacy = next((p.get("mashupLocation") for p in parts if p.get("mashupLocation")), None)
            row = add(name, code, "table", table=table["name"], partitions=[p.get("name", "") for p in parts],
                      storageModes=sorted({p.get("mode") or "" for p in parts} - {""}),
                      group=next((p.get("queryGroup") for p in parts if p.get("queryGroup")), "") or "",
                      load="loaded", resultType=(table.get("annotations") or {}).get("PBI_ResultType") or "",
                      linkedName=legacy or (table.get("annotations") or {}).get("LinkedQueryName") or "")
            if legacy and not _text(code):
                unresolved_legacy += 1
                row["unavailable"] = ("This file keeps the query in a Power Query package that could not be read. Only "
                                      "the placeholder the model stores for the table is present.")
        if len(found) == 1 and len(table.get("partitions") or []) == 1:
            table_rows[table["name"]] = rows[-1]

    for expression in model.get("expressions", []):
        if (expression.get("kind") or "m").lower() != "m":
            continue
        name, code = expression.get("name", ""), expression.get("expression") or ""
        same = table_rows.get(name)
        if same is not None and _text(same["mCode"]) == _text(code):
            same["alsoShared"] = True                      # rule 2
            for key, value in (("group", expression.get("queryGroup")), ("description", expression.get("description")),
                               ("lineageTag", expression.get("lineageTag"))):
                if value and not same.get(key):
                    same[key] = value
            continue
        legacy = bool(expression.get("legacyMashup"))
        add(name, code, "shared", group=expression.get("queryGroup") or "", lineageTag=expression.get("lineageTag") or "",
            description=expression.get("description") or "", resultType=expression.get("resultType") or "",
            load="unknown" if legacy and unresolved_legacy else "not loaded", legacy=legacy)

    # What each query is, and whether all of it was read.
    for row in rows:
        facts = m_steps.read(row["mCode"])
        row["_tokens"] = facts["tokens"]
        row["kind"] = "function" if row.get("resultType") == "Function" and facts["kind"] == "query" else facts["kind"]
        row["extraction"] = dict(facts["extraction"])
        if row.pop("unavailable", None) and row["extraction"]["status"] == "unavailable":
            row["extraction"]["note"] = ("This file keeps the query in a Power Query package that could not be read. "
                                         "Only the placeholder the model stores for the table is present.")
        row["steps"] = {key: facts["steps"][key] for key in ("status", "note", "scope")}

    # Names M can refer to. A shared expression wins over a table of the same name (rule 3): in a model that
    # has both, M names the expression.
    by_name: dict[str, dict] = {}
    for row in rows:
        if row["origin"] == "table" and row["queryName"] == row["table"]:
            by_name[row["queryName"]] = row
            if row.get("linkedName"):
                by_name.setdefault(row["linkedName"], row)
    for row in rows:
        if row["origin"] == "shared":
            by_name[row["queryName"]] = row
    names = set(by_name)
    for row in rows:
        targets = []
        for name in m_steps.references(row.pop("_tokens"), names):
            target = by_name[name]
            if target is not row and target not in targets:
                targets.append(target)
        row["_upstream"] = targets
    # A DirectQuery or Direct Lake entity partition names the shared expression it reads through.
    entity_use: dict[int, set] = {}
    for table in model.get("tables", []):
        for part in table.get("partitions") or []:
            name = ((part.get("source") or {}).get("expressionSource") or "").strip().strip("'")
            target = by_name.get(name) if name else None
            if target is not None and target["origin"] == "shared":
                entity_use.setdefault(id(target), set()).add(table["name"])

    # Tables a query feeds: its own, and every table whose query reaches it through other queries.
    used_by = {id(row): set(entity_use.get(id(row), ())) for row in rows}
    for row in rows:
        if row["origin"] != "table":
            continue
        seen, stack = {id(row)}, list(row["_upstream"])
        while stack:
            target = stack.pop()
            if id(target) in seen:
                continue
            seen.add(id(target))
            used_by[id(target)].add(row["table"])
            stack.extend(target["_upstream"])
    referenced_by = {id(row): [] for row in rows}
    for row in rows:
        for target in row["_upstream"]:
            referenced_by[id(target)].append(row)

    tracer = Tracer(source_definitions(model))
    for row in rows:
        labels, seen = [], set()
        if _text(row["mCode"]):
            for item in materialize(tracer.trace(row["mCode"], row["queryName"])):
                if item.get("sourceType") in (None, "", "Unknown"):
                    continue
                label = source_label(dict(item, detail=item.get("location")))
                if item.get("sourceType") not in INTERNAL_SOURCES and item.get("object") and item["object"] not in label:
                    schema = item.get("schema")
                    label += " · " + (f"{schema}.{item['object']}" if schema else item["object"])
                if label not in seen:
                    seen.add(label)
                    labels.append(label)
        row["sources"] = labels

    rows.sort(key=lambda row: (row["queryName"].casefold(), row["mCode"]))
    taken: set[str] = set()
    for row in rows:
        base = query_id(row["queryName"], row.pop("lineageTag", "") or None)
        object_id, n = base, 2
        while object_id in taken:                          # rule 3: two queries of one name keep an id each
            object_id = f"{base}~{n}"
            n += 1
        taken.add(object_id)
        row["objectId"] = object_id
    file_order = sorted(rows, key=lambda row: (recorded_order.get(row.get("linkedName") or row["queryName"],
                                                                  recorded_order.get(row["queryName"], len(recorded_order))),
                                               row["_file"]))
    for position, row in enumerate(file_order):
        row["order"] = position
    for row in rows:
        own = row.get("table")
        row["usedBy"] = sorted(used_by[id(row)] - ({own} if own else set()), key=str.casefold)
        row["upstream"] = [target["objectId"] for target in row.pop("_upstream")]
        row["referencedBy"] = sorted((r["objectId"] for r in referenced_by[id(row)]), key=str.casefold)
        for key in ("_file", "linkedName", "legacy"):
            row.pop(key, None)
    return rows


def query_groups(model: dict | None) -> list[dict]:
    """The query folders the file records, in the order Power Query shows them."""
    groups = [g for g in (model or {}).get("queryGroups") or [] if isinstance(g, dict) and g.get("folder")]
    return sorted(groups, key=lambda g: (g.get("order") is None, g.get("order") or 0, g["folder"].casefold()))
