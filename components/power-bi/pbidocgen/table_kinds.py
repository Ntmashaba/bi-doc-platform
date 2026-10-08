"""How a table is defined, and what kind of column each column is, from the object's own metadata.

Every table gets exactly one "defined by" kind. It says where the table's rows come from, which is a different
question from the role the table plays in the model (fact, dimension, date dimension: `tableType`, unchanged).

    Calculation group       the table carries a calculation group
    Automatic date table    the file marks it with __PBI_LocalDateTable or __PBI_TemplateDateTable.
                            Only the marker counts: a table merely named LocalDateTable_... is not one
    Power Query             every partition is an M query (or the placeholder a pre-2019 file stores for one)
    SQL query               every partition is a query against a provider data source
    Entity                  every partition names an entity (Direct Lake, DirectQuery to another model)
    Calculated table        every partition is a DAX expression
    Other                   anything else, with the partition types as the file gives them

A column is classified on its own, not from its table: a calculated table can hold calculated columns, and an
imported table can hold them too.

    calculated               a DAX expression per row (a calculated column): the fx marker
    calculatedTableColumn    produced by its table's DAX expression; no expression of its own
    data                     read from the table's source
"""
from __future__ import annotations

KINDS = ("Power Query", "SQL query", "Entity", "Calculated table", "Automatic date table", "Calculation group", "Other")
AUTO_DATE_MARKERS = ("__PBI_LocalDateTable", "__PBI_TemplateDateTable")
_BY_PARTITION = {"m": "Power Query", "query": "SQL query", "entity": "Entity", "calculated": "Calculated table"}
COLUMN_TYPES = ("data", "calculated", "calculatedTableColumn")


def _annotations(table: dict) -> dict:
    found = table.get("annotations") or {}
    if isinstance(found, list):                       # the file's own shape: [{"name": ..., "value": ...}]
        return {a.get("name"): a.get("value") for a in found if isinstance(a, dict)}
    return found if isinstance(found, dict) else {}


def is_automatic_date_table(table: dict) -> bool:
    annotations = _annotations(table)
    return any(name in annotations and str(annotations[name]).strip().lower() not in ("false", "0")
               for name in AUTO_DATE_MARKERS)


def defined_by(table: dict) -> dict:
    """{"kind": one of KINDS, "raw": the partition types as written, only for Other}."""
    partitions = [p for p in table.get("partitions") or [] if isinstance(p, dict)]
    if table.get("calculationGroup") is not None or table.get("calculationGroupDefinition") is not None:
        return {"kind": "Calculation group"}
    if is_automatic_date_table(table):
        return {"kind": "Automatic date table"}
    types = []
    for part in partitions:
        raw = str(part.get("type") or (part.get("source") or {}).get("type") or "")
        if part.get("mashupLocation"):               # a pre-2019 placeholder for a Power Query query
            raw = "m"
        if raw not in types:
            types.append(raw)
    if len(types) == 1 and types[0] in _BY_PARTITION:
        return {"kind": _BY_PARTITION[types[0]]}
    if not partitions:
        return {"kind": "Other", "raw": "no partition"}
    return {"kind": "Other", "raw": ", ".join(t or "unnamed type" for t in types)}


def column_type(column: dict) -> str:
    """From the column's own `type` as the file gives it (TMSL: data, calculated, calculatedTableColumn, rowNumber)."""
    raw = column.get("columnType") or column.get("type")
    if raw in COLUMN_TYPES:
        return raw
    if column.get("isCalculated"):
        return "calculated"
    return str(raw) if raw else "data"
