"""Tabular models stored as XML (ASSL): compatibility levels 1100 and 1103.

Before level 1200 a tabular model is written in the multidimensional vocabulary, as XML. A Model.bim at these
levels and the script SSMS writes for "Script Database as > CREATE To" are the same document: a Database element
inside a Create (or Alter, Batch ...) command. This module reads it into the shape of a model.bim, so everything
after the reader is unchanged:

    table         Dimension, one per table
    column        Dimension Attribute (the internal RowNumber attribute is left out);
                  a calculated column has an ExpressionBinding instead of a ColumnBinding
    partition     Partition of the table's MeasureGroup in the cube: QueryBinding {DataSourceID, QueryDefinition}
    data source   DataSource {Name, ConnectionString}
    measure       CREATE MEASURE 'Table'[Name]=<DAX>; statements in the cube's MdxScript
    relationship  Dimension Relationship; active when a ReferenceMeasureGroupDimension names its RelationshipID
    role          Role, with DatabasePermission and DimensionPermission AllowedRowsExpression

Static text only: nothing is executed and no connection is made. A document type declaration is refused, so
entity expansion cannot be used against the parser. Role members, impersonation accounts and annotations are not
read. Multidimensional cubes use the same XML but are not tabular models, and are refused by name.

The table, column, partition, data source, measure, relationship and role shapes are checked against Microsoft's
1103 scripts (microsoft/Analysis-Services, AlmToolkit test data). Calculated columns, hierarchies and DirectQuery
partitions are written from the documented schema and have not been checked against a real file.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-16", "utf-8")
_DATA_TYPES = {
    "wchar": "string", "char": "string", "bigint": "int64", "integer": "int64", "smallint": "int64",
    "tinyint": "int64", "unsignedbigint": "int64", "unsignedint": "int64", "unsignedsmallint": "int64",
    "unsignedtinyint": "int64", "double": "double", "single": "double", "currency": "decimal",
    "numeric": "decimal", "date": "dateTime", "boolean": "boolean", "binary": "binary",
}
# 'Table'[Measure]= at the start of a statement; '' and ]] escape the quote and the bracket.
_MEASURE = re.compile(r"^[ \t]*CREATE\s+MEASURE\s+'((?:[^']|'')*)'\s*\[((?:[^\]]|\]\])*)\]\s*=", re.I | re.M)
_STATEMENT = re.compile(r"^[ \t]*(?:CREATE\s+(?:MEASURE|KPI|MEMBER|SET)|ALTER\s+CUBE)\b", re.I | re.M)
_KPI = re.compile(r"^[ \t]*CREATE\s+KPI\b", re.I | re.M)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _kids(node, name: str) -> list:
    return [child for child in node if _local(child.tag) == name] if node is not None else []


def _kid(node, name: str):
    return next(iter(_kids(node, name)), None)


def _items(node, collection: str, item: str) -> list:
    """Database/Dimensions/Dimension and the like."""
    return _kids(_kid(node, collection), item)


def _text(node, name: str) -> str:
    child = _kid(node, name)
    return (child.text or "").strip() if child is not None else ""


def _type(node) -> str:
    """The xsi:type of a binding element, without its namespace prefix."""
    if node is None:
        return ""
    return next((value.rsplit(":", 1)[-1] for key, value in node.attrib.items() if _local(key) == "type"), "")


def looks_like_xml(path: Path) -> bool:
    head = Path(path).read_bytes()[:4096]
    for encoding in _ENCODINGS:
        try:
            return head.decode(encoding).lstrip("﻿ \t\r\n").startswith("<")
        except UnicodeDecodeError:
            continue
    return False


def _parse(path: Path):
    raw = path.read_bytes()
    text = None
    for encoding in _ENCODINGS:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"Could not read {path.name} as XML text in any known encoding.")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        raise ValueError(f"{path.name} declares a document type or entities. An Analysis Services definition never "
                         "does, so the file is not read.")
    try:
        # The declared encoding is the one the file was saved in, which the text no longer is.
        return ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("﻿")))
    except ET.ParseError as exc:
        raise ValueError(f"{path.name} is not well-formed XML: {exc}") from None


def _database(root, name: str):
    for node in root.iter():
        if _local(node.tag) == "Database" and (_kid(node, "Dimensions") is not None or _kid(node, "Cubes") is not None):
            return node
    raise ValueError(f"{name} is XML but does not define an Analysis Services database (it may be a processing or "
                     "backup script). In SSMS, right-click the database > Script > Script Database as > CREATE To.")


def _is_tabular(database) -> bool:
    if _text(database, "StorageEngineUsed").lower() == "inmemory":
        return True
    return any(_text(d, "StorageMode").lower() == "inmemory" for d in _items(database, "Dimensions", "Dimension"))


def _column(attribute, names: dict) -> dict:
    key = _kid(_kid(attribute, "KeyColumns"), "KeyColumn")
    source = _kid(key, "Source")
    out = {"name": _text(attribute, "Name") or _text(attribute, "ID"),
           "dataType": _DATA_TYPES.get(_text(key, "DataType").lower(), _text(key, "DataType") or "")}
    if _type(source) == "ExpressionBinding":
        out.update(type="calculated", expression=_text(source, "Expression"))
    else:
        out["sourceColumn"] = _text(source, "ColumnID") or None
    if _text(attribute, "AttributeHierarchyVisible").lower() == "false":
        out["isHidden"] = True
    if _text(attribute, "Description"):
        out["description"] = _text(attribute, "Description")
    order = _text(attribute, "OrderByAttributeID")
    if order and names.get(order) and names[order] != out["name"]:
        out["sortByColumn"] = names[order]
    return out


def _measures(cube) -> tuple[dict, int]:
    """{table name: [measure]} from the MDX script, and the number of KPI statements left unread."""
    properties = {}
    for script in _items(cube, "MdxScripts", "MdxScript"):
        for prop in _items(script, "CalculationProperties", "CalculationProperty"):
            reference = _text(prop, "CalculationReference")
            properties[reference] = prop
    found, kpis = {}, 0
    for script in _items(cube, "MdxScripts", "MdxScript"):
        for command in _items(script, "Commands", "Command"):
            text = _text(command, "Text")
            kpis += len(_KPI.findall(text))
            for match in _MEASURE.finditer(text):
                following = _STATEMENT.search(text, match.end())
                body = text[match.end():following.start() if following else len(text)].strip()
                table = match.group(1).replace("''", "'")
                name = match.group(2).replace("]]", "]")
                measure = {"name": name, "expression": body[:-1].rstrip() if body.endswith(";") else body}
                prop = properties.get(f"[{match.group(2)}]")
                if prop is not None:
                    fmt = _text(prop, "FormatString")
                    if len(fmt) > 2 and fmt[0] == fmt[-1] == "'":
                        measure["formatString"] = fmt[1:-1].replace("''", "'")
                    if _text(prop, "Visible").lower() == "false":
                        measure["isHidden"] = True
                    if _text(prop, "Description"):
                        measure["description"] = _text(prop, "Description")
                    if _text(prop, "DisplayFolder"):
                        measure["displayFolder"] = _text(prop, "DisplayFolder")
                found.setdefault(table, []).append(measure)
    return found, kpis


def _model_permission(permission) -> str:
    read = _text(permission, "Read").lower() == "allowed"
    process = _text(permission, "Process").lower() == "true"
    if _text(permission, "Administer").lower() == "true":
        return "administrator"
    if read and process:
        return "readRefresh"
    return "read" if read else "refresh" if process else "none"


def read_assl_model(path: str | Path) -> dict:
    """An XML tabular model (Model.bim or a CREATE script, level 1100 or 1103) as a model.bim document."""
    path = Path(path)
    database = _database(_parse(path), path.name)
    if not _is_tabular(database):
        raise ValueError(f"{path.name} defines a multidimensional database (cubes and dimensions), not a tabular "
                         "model. Multidimensional definitions are not read.")
    warnings = []
    dimensions = _items(database, "Dimensions", "Dimension")
    table_name = {_text(d, "ID"): _text(d, "Name") or _text(d, "ID") for d in dimensions}
    sources = {_text(s, "ID"): s for s in _items(database, "DataSources", "DataSource")}
    source_name = {key: _text(s, "Name") or key for key, s in sources.items()}
    cubes = _items(database, "Cubes", "Cube")
    cube = cubes[0] if cubes else None
    if len(cubes) > 1:
        warnings.append(f"The definition has {len(cubes)} cubes; partitions and measures were read from the first only.")

    # Each table's partitions are in the measure group whose own (degenerate) dimension is that table.
    cube_dimension = {_text(d, "ID"): _text(d, "DimensionID") or _text(d, "ID") for d in _items(cube, "Dimensions", "Dimension")}
    hidden_tables = {cube_dimension[_text(d, "ID")] for d in _items(cube, "Dimensions", "Dimension")
                     if _text(d, "Visible").lower() == "false"}
    partitions, active = {}, set()
    for group in _items(cube, "MeasureGroups", "MeasureGroup"):
        own = next((_text(d, "CubeDimensionID") for d in _items(group, "Dimensions", "Dimension")
                    if _type(d) == "DegenerateMeasureGroupDimension"), "")
        table = cube_dimension.get(own, own) if own else _text(group, "ID")
        if table not in table_name:
            table = _text(group, "ID")
        for dimension in _items(group, "Dimensions", "Dimension"):
            if _text(dimension, "RelationshipID"):
                active.add(_text(dimension, "RelationshipID"))
        for partition in _items(group, "Partitions", "Partition"):
            binding = _kid(partition, "Source")
            name = _text(partition, "Name") or _text(partition, "ID")
            out = {"name": name, "source": {"type": "query"}}
            if _text(partition, "StorageMode").lower() == "directquery":
                out["mode"] = "directQuery"
            if _type(binding) == "QueryBinding":
                out["source"]["query"] = _text(binding, "QueryDefinition")
                out["source"]["dataSource"] = source_name.get(_text(binding, "DataSourceID"), _text(binding, "DataSourceID"))
            else:
                out["source"]["query"] = ""
                warnings.append(f"Partition '{name}' of '{table_name.get(table, table)}' has a "
                                f"{_type(binding) or 'missing'} source, which is not read.")
            partitions.setdefault(table, []).append(out)

    # A table with no partition in the cube still has its defining query in the data source view.
    view_queries = {}
    for view in _items(database, "DataSourceViews", "DataSourceView"):
        for node in view.iter():
            query = next((v for k, v in node.attrib.items() if _local(k) == "QueryDefinition"), None)
            if _local(node.tag) == "element" and query and node.attrib.get("name"):
                view_queries.setdefault(node.attrib["name"], (query, _text(view, "DataSourceID")))

    measures, kpis = _measures(cube) if cube is not None else ({}, 0)
    if kpis:
        warnings.append(f"{kpis} KPI definition(s) in the model were not read; their base measures were.")
    tables, attribute_name, relationships, seen = [], {}, [], set()
    for dimension in dimensions:
        key = _text(dimension, "ID")
        attributes = [a for a in _items(dimension, "Attributes", "Attribute") if _text(a, "Type") != "RowNumber"]
        names = {_text(a, "ID"): _text(a, "Name") or _text(a, "ID") for a in attributes}
        attribute_name[key] = names
        table = {"name": table_name[key], "columns": [_column(a, names) for a in attributes]}
        if _text(dimension, "Description"):
            table["description"] = _text(dimension, "Description")
        if key in hidden_tables:
            table["isHidden"] = True
        table["partitions"] = partitions.get(key, [])
        if not table["partitions"] and key in view_queries:
            query, source = view_queries[key]
            table["partitions"] = [{"name": table["name"], "source": {
                "type": "query", "query": query, "dataSource": source_name.get(source, source)}}]
        if measures.get(table["name"]):
            table["measures"] = measures.pop(table["name"])
        hierarchies = [{"name": _text(h, "Name") or _text(h, "ID"),
                        "levels": [{"name": _text(level, "Name"), "column": names.get(_text(level, "SourceAttributeID"), _text(level, "SourceAttributeID"))}
                                   for level in _items(h, "Levels", "Level")]}
                       for h in _items(dimension, "Hierarchies", "Hierarchy")]
        if hierarchies:
            table["hierarchies"] = hierarchies
        tables.append(table)
    for table, orphans in measures.items():
        warnings.append(f"{len(orphans)} measure(s) are defined on '{table}', which is not a table in the model.")

    any_relationship_ids = bool(active)
    for dimension in dimensions:
        for relationship in _items(dimension, "Relationships", "Relationship"):
            key = _text(relationship, "ID")
            if key in seen:
                continue
            seen.add(key)
            ends = []
            for end in (_kid(relationship, "FromRelationshipEnd"), _kid(relationship, "ToRelationshipEnd")):
                table = _text(end, "DimensionID")
                attribute = _text(_kid(_kid(end, "Attributes"), "Attribute"), "AttributeID")
                ends.append((table_name.get(table, table), attribute_name.get(table, {}).get(attribute, attribute),
                             _text(end, "Multiplicity").lower() or None))
            out = {"name": key, "fromTable": ends[0][0], "fromColumn": ends[0][1],
                   "toTable": ends[1][0], "toColumn": ends[1][1]}
            if ends[0][2]:
                out["fromCardinality"] = ends[0][2]
            if ends[1][2]:
                out["toCardinality"] = ends[1][2]
            if any_relationship_ids:
                out["isActive"] = key in active
            relationships.append(out)
    if relationships and not any_relationship_ids:
        warnings.append("The definition does not say which relationships are active; all are shown as active.")

    permission = {_text(p, "RoleID"): p for p in _items(database, "DatabasePermissions", "DatabasePermission")}
    filters = {}
    for dimension in dimensions:
        for allowed in _items(dimension, "DimensionPermissions", "DimensionPermission"):
            if _text(allowed, "AllowedRowsExpression"):
                filters.setdefault(_text(allowed, "RoleID"), []).append(
                    {"name": table_name[_text(dimension, "ID")], "filterExpression": _text(allowed, "AllowedRowsExpression")})
    roles = []
    for role in _items(database, "Roles", "Role"):
        key = _text(role, "ID")
        out = {"name": _text(role, "Name") or key,
               "modelPermission": _model_permission(permission[key]) if key in permission else "none"}
        if filters.get(key):
            out["tablePermissions"] = filters[key]
        roles.append(out)

    level = _text(database, "CompatibilityLevel")
    model = {"dataSources": [{"name": source_name[key], "connectionString": _text(s, "ConnectionString")}
                             for key, s in sources.items()],
             "tables": tables, "relationships": relationships, "roles": roles}
    doc = {"name": _text(database, "Name") or _text(database, "ID") or path.stem, "model": model,
           "_readerWarnings": warnings}
    if level.isdigit():
        doc["compatibilityLevel"] = int(level)
    return doc
