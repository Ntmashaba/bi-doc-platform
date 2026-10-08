"""Object identity and the search index of a generated document.

Identity is `pbi-identity/1` (docs/contracts/identity-and-bindings-v1.md). A table, column, measure or shared
query is keyed on its lineage tag, which Power BI keeps when the object is renamed, and on its name when the
file carries no tag. Pages and visuals are keyed on the ids the report gives them. The platform adapter
(`bidoc_engines.power_bi`) uses the same functions, so an object has one id in the document, in its links and in
the library.

The index lists every named object once: its id, what kind of object it is, its name and its parent (the table
of a column, the page of a visual). It is built from the payload that is about to be rendered, never from the
input, so whatever a publication profile withheld from the page is absent from the index too.
"""
from __future__ import annotations

import hashlib
import json

from .table_kinds import defined_by

INDEX_VERSION = 2

# Kinds in the order the document is read: sources, queries, the model, then the report.
KINDS = ("data source", "query", "table", "calculated table", "automatic date table", "calculation group", "column",
         "calculated column", "measure", "security role", "page", "visual")

SOURCE_FIELDS = ("sourceType", "server", "database", "schema", "object", "location")


def table_id(table: dict) -> str:
    tag = table.get("lineageTag")
    return f"pbi:table:{tag}" if tag else f"pbi:table:name:{table.get('name', '')}"


def measure_id(table_name: str, measure: dict) -> str:
    tag = measure.get("lineageTag")
    return f"pbi:measure:{tag}" if tag else f"pbi:measure:name:{table_name}/{measure.get('name', '')}"


def column_id(table_name: str, column: dict) -> str:
    tag = column.get("lineageTag")
    return f"pbi:column:{tag}" if tag else f"pbi:column:name:{table_name}/{column.get('name', '')}"


def query_id(name: str, lineage_tag: str | None = None) -> str:
    return f"pbi:query:{lineage_tag}" if lineage_tag else f"pbi:query:name:{name}"


def page_id(page: dict) -> str:
    return f"pbi:page:{page.get('id') or page.get('name') or ''}"


def visual_id(page: dict, visual: dict) -> str:
    return f"pbi:visual:{page.get('id') or page.get('name') or ''}/{visual.get('id') or ''}"


def role_id(name: str) -> str:
    return f"pbi:role:{name}"


def source_fields(row: dict) -> list[str]:
    """The fields that make two source rows the same external source (as the Data sources list groups them)."""
    return [str(row.get(key) or "") for key in SOURCE_FIELDS]


def source_id(fields: list[str]) -> str:
    """Opaque and stable while the source's identity stays the same; never spells the location."""
    digest = hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"pbi:datasource:{digest[:16]}"


LIVE_SOURCE_ID = "pbi:datasource:live"


_TABLE_KINDS = {"Calculation group": "calculation group", "Calculated table": "calculated table",
                "Automatic date table": "automatic date table"}


def table_kind(table: dict) -> str:
    """The kind a table is listed under, from how it is defined (`table_kinds.defined_by`): a calculation group,
    a calculated table, an automatic date table, or a table."""
    defined = table.get("definedBy") if isinstance(table.get("definedBy"), dict) else defined_by(table)
    return _TABLE_KINDS.get(defined.get("kind"), "table")


def _entries(payload: dict):
    """(kind, name or None, parent, id, locator...) for every named object in the payload."""
    model = payload.get("model") or {}
    report = payload.get("report") or {}

    groups = {}
    for row in (payload.get("primarySources") or {}).get("rows") or []:
        if isinstance(row, dict):
            fields = source_fields(row)
            groups.setdefault(tuple(fields), fields)
    for fields in groups.values():
        # The page names a source the way its Data sources list does, so the name is filled in there.
        yield ("data source", None, fields[0], source_id(fields), fields)
    live = payload.get("liveSource")
    if isinstance(live, dict):
        yield ("data source", live.get("label") or live.get("sourceType") or "Live connection", "Live connection",
               LIVE_SOURCE_ID, "live")

    for row in payload.get("sourceQueries") or []:
        if isinstance(row, dict) and row.get("queryName"):
            yield ("query", row["queryName"], row.get("group") or "", row.get("objectId") or query_id(row["queryName"]))

    for table in model.get("tables") or []:
        name = table.get("name", "")
        yield (table_kind(table), name, "", table_id(table))
        for column in table.get("columns") or []:
            yield ("calculated column" if column.get("isCalculated") else "column", column.get("name", ""), name,
                   column_id(name, column))
        for measure in table.get("measures") or []:
            yield ("measure", measure.get("name", ""), name, measure_id(name, measure))

    for role in model.get("roles") or []:
        if role.get("name"):
            yield ("security role", role["name"], "", role_id(role["name"]))

    for page in report.get("pages") or []:
        pid = str(page.get("id") or page.get("name") or "")
        yield ("page", page.get("name") or pid, report.get("name") or "", page_id(page), pid)
        for visual in page.get("visuals") or []:
            # An untitled visual is named on the page from its type and first field ("Card · Amount").
            yield ("visual", visual.get("title") or None, page.get("name") or pid, visual_id(page, visual), pid,
                   str(visual.get("id") or ""))


def build_search_index(payload: dict) -> dict:
    """The index embedded in the page: {"v", "kinds", "parents", "items": [[kind, name, parent, id, ...]]}.

    Kinds and parents are stored once and referred to by position, which keeps a model with thousands of
    columns small. Items are in kind order, then by name, so the page can list them without sorting.
    """
    rank = {kind: i for i, kind in enumerate(KINDS)}
    entries = sorted(_entries(payload), key=lambda e: (rank[e[0]], (e[1] or "￿").casefold(), e[2].casefold(), e[3]))
    parents, parent_index, items, seen = [], {}, [], set()
    for kind, name, parent, object_id, *locator in entries:
        base, n = object_id, 2
        while object_id in seen:          # two objects sharing a tag or a name still get an id each
            object_id = f"{base}~{n}"
            n += 1
        seen.add(object_id)
        if parent and parent not in parent_index:
            parent_index[parent] = len(parents)
            parents.append(parent)
        items.append([rank[kind], name, parent_index.get(parent, -1), object_id, *locator])
    return {"v": INDEX_VERSION, "kinds": list(KINDS), "parents": parents, "items": items}
