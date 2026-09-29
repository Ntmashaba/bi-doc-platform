"""Best-effort source metadata for the Tables summary; no database connection."""
from __future__ import annotations

import re

_TOKEN = re.compile(r'"(?:[^"]|"")*"|//[^\n]*|/\*.*?\*/', re.S)
_FUNCTION = re.compile(r'\b(Sql\.Database[s]?|Value\.NativeQuery|Odbc\.Query)\s*\(', re.I)


def _mask(text, strings=True):
    return _TOKEN.sub(lambda m: " " * len(m[0]) if strings or not m[0].startswith('"') else m[0], text)


def _split(text):
    """Split top-level arguments/record fields without splitting SQL literals."""
    mask, depth, start, parts = _mask(text), 0, 0, []
    for i, char in enumerate(mask):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


def _literal(value):
    value = _mask(value, strings=False).strip()
    if not re.fullmatch(r'"(?:[^"]|"")*"', value, re.S):
        return None
    value = value[1:-1].replace('""', '"')
    escapes = {"lf": "\n", "cr": "\r", "tab": "\t", "#": "#"}
    def decode(match):
        codes = match[1].split(",")
        return "".join(escapes[c.strip()] for c in codes) if all(c.strip() in escapes for c in codes) else match[0]
    return re.sub(r'#\(([^)]+)\)', decode, value)


def _calls(text):
    mask = _mask(text)
    for match in _FUNCTION.finditer(mask):
        start, depth = match.end(), 1
        for end in range(start, len(mask)):
            if mask[end] == "(":
                depth += 1
            elif mask[end] == ")":
                depth -= 1
                if depth == 0:
                    yield match[1].lower(), _split(text[start:end])
                    break


def _record(text):
    if not (text.strip().startswith("[") and text.strip().endswith("]")):
        return {}
    return {key.strip().lower(): value.strip() for item in _split(text.strip()[1:-1])
            for key, sep, value in [item.partition("=")] if sep}


def enrich_source(source, expression, mode, data_source=None):
    """Add explicit object type/query text; keep unresolved expressions visible."""
    source = dict(source, query=None, queryKind=None, objectType=None, notes=[])
    if mode == "calculated":
        source.update(objectType="Calculated table", query=expression, queryKind="DAX",
                      server=None, database=None)
        return source
    if mode == "entity":
        source["objectType"] = "Entity"
        # expressionSource names a model expression, not a physical database.
        if source.get("database"):
            source["notes"].append("Connection expression: " + source["database"])
            source["database"] = None
        return source
    if mode == "query":
        source.update(query=expression, queryKind="SQL", nativeQuery=True, objectType="SQL query")
        ds = data_source or {}
        address = (ds.get("connectionDetails") or {}).get("address") or {}
        source["server"] = address.get("server")
        source["database"] = address.get("database")
        # Whitelist metadata; never copy credentials from a connection string.
        connection = ds.get("connectionString") or ""
        for key, value in re.findall(r'(?:^|;)\s*([^=;]+)=\s*("[^"]*"|\{[^}]*\}|[^;]*)', connection):
            key = key.strip().lower()
            value = value.strip().strip('"{}')
            if key in ("data source", "server", "address", "addr", "network address"):
                source["server"] = value
            elif key in ("initial catalog", "database"):
                source["database"] = value
        source["sourceType"] = "SQL query source"
        return source

    calls = list(_calls(expression))
    if source.get("sourceType") == "Snowflake":
        # Snowflake.Databases takes a warehouse as its second argument.
        source["database"] = None
    sql_connections = [args for fn, args in calls if fn.startswith("sql.database")]
    sql_queries = []
    unresolved = False
    for fn, args in calls:
        if fn in ("value.nativequery", "odbc.query") and len(args) > 1:
            query = _literal(args[1])
            if query is not None:
                sql_queries.append(query)
            else:
                unresolved = True
        if fn == "sql.database" and len(args) > 2:
            record = _record(args[2])
            if "query" in record:
                query = _literal(record["query"])
                if query is not None:
                    sql_queries.append(query)
                else:
                    unresolved = True

    if len(sql_connections) == 1:
        args = sql_connections[0]
        source["server"] = _literal(args[0]) if args else None
        source["database"] = _literal(args[1]) if len(args) > 1 else None
        if source["server"] is None or (len(args) > 1 and source["database"] is None):
            source["notes"].append("Server/database use an expression or parameter; not resolved statically.")
    elif len(sql_connections) > 1:
        source.update(server=None, database=None, schema=None, object=None)
        source["notes"].append("Multiple SQL connections in this partition; inspect the Power Query expression for source associations.")

    # Navigation records explicitly state Item/Schema/Kind. Do not infer View
    # from a name such as vwSales; many SQL navigations omit Kind entirely.
    navigations = []
    mask = _mask(expression)
    for match in re.finditer(r'\{\s*\[', mask):
        end = mask.find("]", match.end())
        if end < 0:
            continue
        start = mask.find("[", match.start())
        record = {key: _literal(value) for key, value in _record(expression[start:end + 1]).items()}
        if len(sql_connections) > 1:
            continue
        if (record.get("kind") or "").lower() == "database" and not source["database"]:
            source["database"] = record.get("name") or record.get("item")
        if record.get("schema") or (record.get("kind") or "").lower() in ("table", "view"):
            navigations.append(record)
        elif record.get("name") and not source["database"] and len(sql_connections) == 1:
            source["database"] = record["name"]
    if len(sql_connections) <= 1 and len(navigations) == 1:
        record = navigations[0]
        source["schema"] = record.get("schema")
        source["object"] = record.get("item") or record.get("name")
        source["objectType"] = (record.get("kind") or "Table or view").capitalize()
    elif len(navigations) > 1:
        source.update(schema=None, object=None)
        source["notes"].append("Multiple navigation objects; inspect the Power Query expression for the final source mapping.")

    if sql_queries and len(sql_queries) == 1 and not unresolved and len(sql_connections) <= 1:
        source.update(query=sql_queries[0], queryKind="SQL", objectType="SQL query", nativeQuery=True)
    elif sql_queries or unresolved:
        source.update(query=expression, queryKind="Power Query (M)", objectType="Query (review expression)", nativeQuery=True)
        source["notes"].append("SQL is dynamic or multiple queries are present; full M is shown instead of a partial SQL statement.")
    if not source.get("objectType"):
        source["objectType"] = "Table or view" if source.get("object") else "Power Query"
    if not source.get("object") and not source.get("query"):
        source.update(query=expression, queryKind="Power Query (M)")
    return source


def build_source_inventory(model, report, linked, columns):
    rows = []
    for table in model["tables"]:
        for part in table["partitions"] or [{"name": "", "source": {}, "expression": ""}]:
            source = part["source"]
            obj = source.get("object") or ""
            if not obj and source.get("sourceType") in ("Excel workbook", "CSV file", "SharePoint file", "SharePoint files", "SharePoint list", "Web / API", "OData"):
                obj = source.get("detail") or ""
            schema = source.get("schema")
            if schema and obj and not obj.startswith(schema + "."):
                obj = schema + "." + obj
            rows.append({"report": report["name"] if report else "Not supplied", "table": table["name"],
                         "partition": part["name"], "server": source.get("server") or "",
                         "database": source.get("database") or "", "sourceType": source.get("sourceType") or "Unknown",
                         "objectType": source.get("objectType") or "Unknown", "object": obj,
                         "query": source.get("query") or "", "queryKind": source.get("queryKind") or "",
                         "expression": part["expression"], "notes": source.get("notes", [])})
    # Retain partitions, but resolve report usage at page grain. Blank-page
    # rows identify unassigned/report-only scope rather than inventing a page.
    return [dict(row, page=usage["page"], pageId=usage["pageId"],
                 pageScope=usage["scope"], usage=usage["usage"],
                 pageFields=usage["fields"], pageMeasures=usage["measures"], pageEvidence=usage["evidence"])
            for row in rows for usage in (columns or {}).get("tablePages", []) if usage["table"] == row["table"]]
