"""Read a TMDL semantic model folder and emit the TMSL shape.

Power BI projects (.pbip) save the semantic model in one of two formats:

    Sales.SemanticModel/model.bim            TMSL  — a single JSON document
    Sales.SemanticModel/definition/*.tmdl    TMDL  — a folder of text files

TMDL is now the Desktop default. Rather than write a second analysis pipeline,
this module parses TMDL into exactly the dictionary shape `load_json_lenient`
would have returned for a .bim, so `parse_model` — and therefore the linker,
the warnings, and all four renderers — are entirely unaware of which format
the project used.

TMDL grammar, as much of it as matters here
-------------------------------------------
Indentation (tabs, or spaces) defines nesting. A line is one of:

    /// a description, attached to the object declared below it
    // a comment
    key: value                          a property
    isHidden                            a bare boolean property (means true)
    keyword Name                        an object with a child block
    keyword Name = inline value         an object with an inline value
    keyword Name =                      an object whose value is the block
        <deeper-indented lines>         below it, e.g. DAX or M
    keyword Name = ```                  the same, explicitly fenced
        ...
        ```

Object names containing spaces or punctuation are single-quoted, and an
embedded quote is doubled: 'Bob''s Table'.

Known limits
------------
* An expression block indented no deeper than its own object's properties is
  ambiguous; lines that look like properties end the expression. Desktop always
  indents expressions deeper, so this only affects hand-edited files.
* Translations (cultures/) and perspectives are not read — nothing downstream
  consumes them.
"""

from __future__ import annotations

import re
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be")

# Keywords that declare an object rather than set a property. Anything else
# appearing alone on a line is a bare boolean property (`isHidden`).
_OBJECT_KEYWORDS = {
    "model", "database", "table", "column", "measure", "hierarchy", "level",
    "partition", "relationship", "role", "tablePermission", "annotation",
    "expression", "calculationGroup", "calculationItem", "perspective",
    "culture", "ref", "changedProperty", "extendedProperty", "variation",
    "queryGroup", "linguisticMetadata", "dataAccessOptions", "namedExpression",
    "source", "sourceLineage", "refreshPolicy", "translations", "object",
    "calculationGroupExpression", "formatStringDefinition", "detailRowsDefinition",
    "dataSource",
}

_PROPERTY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_DECL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$", re.S)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _depth(line: str) -> int:
    stripped = line.lstrip("\t ")
    return len(line[: len(line) - len(stripped)].expandtabs(4))


def _unquote(name: str) -> str:
    """'Bob''s Table' -> Bob's Table"""
    name = (name or "").strip()
    if len(name) >= 2 and name[0] == "'" and name[-1] == "'":
        return name[1:-1].replace("''", "'")
    return name


def _unescape_value(value: str) -> str:
    """TMDL escapes a handful of leading characters in scalar values."""
    value = (value or "").strip()
    if value.startswith("\\"):
        value = value[1:]
    return value


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("", "true", "1", "yes")


class Node:
    """One declared object: `keyword Name [= value]` plus its child block."""

    __slots__ = ("keyword", "name", "value", "expression", "properties",
                 "children", "description")

    def __init__(self, keyword: str, name: str = "", value: str | None = None):
        self.keyword = keyword
        self.name = name
        self.value = value              # inline value after '='
        self.expression: str | None = None  # block value after a bare '='
        self.properties: dict[str, str] = {}
        self.children: list[Node] = []
        self.description: str | None = None

    def kids(self, keyword: str):
        return [c for c in self.children if c.keyword == keyword]

    def kid(self, keyword: str):
        for c in self.children:
            if c.keyword == keyword:
                return c
        return None

    def prop(self, key: str, default=None):
        return self.properties.get(key, default)

    def text(self) -> str:
        """The object's value, inline or block, whichever was used."""
        if self.expression is not None:
            return self.expression
        return self.value or ""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Node {self.keyword} {self.name!r} props={len(self.properties)} kids={len(self.children)}>"


# --------------------------------------------------------------------------
# Line-oriented parser
# --------------------------------------------------------------------------

def parse_tmdl(text: str) -> list[Node]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    nodes, _ = _parse_block(lines, 0, 0)
    return nodes


def _next_content(lines: list[str], i: int) -> int:
    while i < len(lines) and not lines[i].strip():
        i += 1
    return i


def _parse_block(lines: list[str], i: int, depth: int) -> tuple[list[Node], int]:
    nodes: list[Node] = []
    pending_desc: list[str] = []

    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue

        d = _depth(raw)
        if d < depth:
            break
        if d > depth:
            # content deeper than expected (already-consumed block remnants)
            i += 1
            continue

        s = raw.strip()

        if s.startswith("///"):
            pending_desc.append(s[3:].strip())
            i += 1
            continue
        if s.startswith("//"):
            i += 1
            continue

        prop = _PROPERTY_RE.match(s)
        decl = _DECL_RE.match(s)
        keyword = decl.group(1) if decl else ""

        # `key: value` is always a property — an object declaration is
        # `keyword Name`, never `keyword: Name`.
        if prop:
            key, value = prop.group(1), prop.group(2).strip()
            if value == "" or value == "```":
                value, i = _consume_value_block(lines, i + 1, depth,
                                                fenced=(value == "```"))
                _stash_property(nodes, key, value)
                continue
            _stash_property(nodes, key, _unescape_value(value))
            i += 1
            continue

        if not decl:
            i += 1
            continue

        rest = decl.group(2).strip()

        # bare boolean property, e.g. `isHidden`
        if not rest and keyword not in _OBJECT_KEYWORDS:
            _stash_property(nodes, keyword, "true")
            i += 1
            continue

        name_part, sep, inline = rest.partition("=")
        node = Node(keyword, _unquote(name_part), inline.strip() if sep else None)
        if pending_desc:
            node.description = "\n".join(pending_desc)
            pending_desc = []

        i += 1
        if sep and (node.value == "" or node.value == "```"):
            fenced = node.value == "```"
            node.expression, i = _consume_value_block(lines, i, depth, fenced=fenced)
            node.value = None

        node.children, i = _parse_block(lines, i, _child_depth(lines, i, depth))
        _absorb_properties(node)
        nodes.append(node)

    return nodes, i


def _child_depth(lines: list[str], i: int, depth: int) -> int:
    """Actual indent of the child block, whatever width the file uses."""
    j = _next_content(lines, i)
    if j < len(lines):
        d = _depth(lines[j])
        if d > depth:
            return d
    return depth + 1


def _stash_property(nodes: list[Node], key: str, value: str) -> None:
    """Attach a property to the node currently being built.

    Properties belonging to a node are parsed inside that node's child block,
    so they arrive as pseudo-nodes; `_absorb_properties` moves them onto the
    parent. This helper handles the block-level case.
    """
    holder = Node("__property__", key, value)
    nodes.append(holder)


def _absorb_properties(node: Node) -> None:
    kept: list[Node] = []
    for child in node.children:
        if child.keyword == "__property__":
            node.properties[child.name] = child.value or ""
        else:
            kept.append(child)
    node.children = kept


def _consume_value_block(lines: list[str], i: int, owner_depth: int,
                         fenced: bool = False) -> tuple[str, int]:
    """Read a multi-line value (DAX, M, a filter expression) after a bare '='."""
    if fenced:
        body: list[str] = []
        while i < len(lines):
            if lines[i].strip() == "```":
                return _dedent(body), i + 1
            body.append(lines[i])
            i += 1
        return _dedent(body), i

    j = _next_content(lines, i)
    if j >= len(lines):
        return "", j
    block_depth = _depth(lines[j])
    if block_depth <= owner_depth:
        return "", i

    body = []
    i = j
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            body.append("")
            i += 1
            continue
        d = _depth(line)
        if d < block_depth:
            break
        # Guard the ambiguous case: an expression indented level with its own
        # object's properties. A property-looking line ends the expression.
        if d == block_depth == owner_depth + 4:
            s = line.strip()
            if _PROPERTY_RE.match(s) and s.split(":")[0].strip() not in ("let", "in"):
                break
        body.append(line)
        i += 1

    while body and not body[-1].strip():
        body.pop()
    return _dedent(body), i


def _dedent(body: list[str]) -> str:
    widths = [_depth(l) for l in body if l.strip()]
    cut = min(widths) if widths else 0
    out = []
    for line in body:
        if not line.strip():
            out.append("")
            continue
        expanded = line.expandtabs(4)
        out.append(expanded[cut:] if len(expanded) >= cut else expanded.lstrip())
    return "\n".join(out)


# --------------------------------------------------------------------------
# TMDL -> TMSL mapping
# --------------------------------------------------------------------------

_COLUMN_PROPS = {
    "dataType": "dataType", "formatString": "formatString",
    "sortByColumn": "sortByColumn", "dataCategory": "dataCategory",
    "displayFolder": "displayFolder", "summarizeBy": "summarizeBy",
    "sourceColumn": "sourceColumn", "lineageTag": "lineageTag",
}
_MEASURE_PROPS = {
    "formatString": "formatString", "displayFolder": "displayFolder", "lineageTag": "lineageTag",
}


def _split_ref(ref: str) -> tuple[str, str]:
    """`Sales.CustomerId`, `'Fact Sales'.CustomerId`, `Sales.'Customer Id'`."""
    ref = (ref or "").strip()
    if ref.startswith("'"):
        end = 1
        while end < len(ref):
            if ref[end] == "'":
                if end + 1 < len(ref) and ref[end + 1] == "'":
                    end += 2
                    continue
                break
            end += 1
        table = ref[1:end].replace("''", "'")
        rest = ref[end + 1:].lstrip(".")
        return table, _unquote(rest)
    table, _, column = ref.partition(".")
    return table.strip(), _unquote(column)


def _table_to_tmsl(node: Node) -> dict:
    out: dict = {"name": node.name}
    if node.description:
        out["description"] = node.description
    if _as_bool(node.prop("isHidden", False)) and "isHidden" in node.properties:
        out["isHidden"] = True
    if node.prop("dataCategory"):
        out["dataCategory"] = node.prop("dataCategory")
    if node.prop("lineageTag"):
        out["lineageTag"] = _unescape_value(node.prop("lineageTag"))

    columns = []
    for c in node.kids("column"):
        col: dict = {"name": c.name}
        for tmdl_key, tmsl_key in _COLUMN_PROPS.items():
            if c.prop(tmdl_key) is not None:
                col[tmsl_key] = _unescape_value(c.prop(tmdl_key))
        if c.description:
            col["description"] = c.description
        if "isHidden" in c.properties:
            col["isHidden"] = _as_bool(c.prop("isHidden"))
        if "isKey" in c.properties:
            col["isKey"] = _as_bool(c.prop("isKey"))
        if "isNullable" in c.properties:
            col["isNullable"] = _as_bool(c.prop("isNullable"))
        expr = c.text()
        if expr:
            col["type"] = "calculated"
            col["expression"] = expr
        elif c.prop("type"):
            col["type"] = c.prop("type")
        columns.append(col)
    out["columns"] = columns

    measures = []
    for m in node.kids("measure"):
        mea: dict = {"name": m.name, "expression": m.text()}
        for tmdl_key, tmsl_key in _MEASURE_PROPS.items():
            if m.prop(tmdl_key) is not None:
                mea[tmsl_key] = _unescape_value(m.prop(tmdl_key))
        if m.description:
            mea["description"] = m.description
        if "isHidden" in m.properties:
            mea["isHidden"] = _as_bool(m.prop("isHidden"))
        # a format-string expression is a child object, not a property
        fsd = m.kid("formatStringDefinition")
        if fsd is not None:
            mea["formatStringDefinition"] = {"expression": fsd.text()}
        detail = m.kid("detailRowsDefinition")
        if detail is not None:
            mea["detailRowsDefinition"] = {"expression": detail.text()}
        measures.append(mea)
    out["measures"] = measures

    hierarchies = []
    for h in node.kids("hierarchy"):
        levels = []
        for lv in h.kids("level"):
            levels.append({
                "name": lv.name,
                "column": _unquote(lv.prop("column", "")),
                "ordinal": int(lv.prop("ordinal", len(levels)) or len(levels)),
            })
        levels.sort(key=lambda x: x["ordinal"])
        hierarchies.append({"name": h.name, "levels": levels})
    if hierarchies:
        out["hierarchies"] = hierarchies

    partitions = []
    for p in node.kids("partition"):
        # `partition Sales = m` / `= calculated` / `= entity` / `= query`
        ptype = (p.value or "m").strip() or "m"
        source: dict = {"type": ptype}
        src_node = p.kid("source")
        if src_node is not None:
            expr = src_node.text()
            if expr:
                source["expression"] = expr
            for key, val in src_node.properties.items():
                source[key] = _unescape_value(val)
        elif p.prop("source") is not None:
            source["expression"] = p.prop("source")
        partition = {
            "name": p.name or node.name,
            "mode": p.prop("mode", "import"),
            "source": source,
        }
        if p.prop("queryGroup"):
            partition["queryGroup"] = _unquote(p.prop("queryGroup"))
        partitions.append(partition)
    out["partitions"] = partitions

    detail = node.kid("detailRowsDefinition")
    if detail is not None:
        out["detailRowsDefinition"] = {"expression": detail.text()}
    cg = node.kid("calculationGroup")
    if cg is not None:
        out["calculationGroup"] = {
            "precedence": cg.prop("precedence"),
            "calculationItems": [
                {"name": ci.name, "expression": ci.text(),
                 "formatStringDefinition": {"expression": ci.kid("formatStringDefinition").text()} if ci.kid("formatStringDefinition") else None}
                for ci in cg.kids("calculationItem")
            ],
        }

    annotations = [{"name": a.name, "value": a.text()} for a in node.kids("annotation")]
    if annotations:
        out["annotations"] = annotations
    return out


def _annotations(node: Node) -> list[dict]:
    return [{"name": a.name, "value": a.text()} for a in node.kids("annotation")]


def _expression_to_tmsl(node: Node) -> dict:
    """A shared expression with the query facts written beside it: folder, lineage tag, result type."""
    out: dict = {"name": node.name, "kind": node.prop("kind", "m"), "expression": node.text()}
    if node.prop("lineageTag"):
        out["lineageTag"] = _unescape_value(node.prop("lineageTag"))
    if node.prop("queryGroup"):
        out["queryGroup"] = _unquote(node.prop("queryGroup"))
    if node.description:
        out["description"] = node.description
    annotations = _annotations(node)
    if annotations:
        out["annotations"] = annotations
    return out


def _query_group_to_tmsl(node: Node) -> dict:
    out: dict = {"folder": node.name}
    description = node.description or node.prop("description")
    if description:
        out["description"] = description
    annotations = _annotations(node)
    if annotations:
        out["annotations"] = annotations
    return out


def _relationship_to_tmsl(node: Node) -> dict:
    from_table, from_column = _split_ref(node.prop("fromColumn", ""))
    to_table, to_column = _split_ref(node.prop("toColumn", ""))
    rel = {
        "name": node.name,
        "fromTable": from_table, "fromColumn": from_column,
        "toTable": to_table, "toColumn": to_column,
        "fromCardinality": node.prop("fromCardinality", "many"),
        "toCardinality": node.prop("toCardinality", "one"),
        "crossFilteringBehavior": node.prop("crossFilteringBehavior", "oneDirection"),
    }
    if "isActive" in node.properties:
        rel["isActive"] = _as_bool(node.prop("isActive"))
    if node.prop("securityFilteringBehavior"):
        rel["securityFilteringBehavior"] = node.prop("securityFilteringBehavior")
    if node.prop("joinOnDateBehavior"):
        rel["joinOnDateBehavior"] = node.prop("joinOnDateBehavior")
    return rel


def _role_to_tmsl(node: Node) -> dict:
    permissions = []
    for tp in node.kids("tablePermission"):
        permissions.append({
            "name": tp.name,
            "filterExpression": tp.text(),
        })
    return {
        "name": node.name,
        "modelPermission": node.prop("modelPermission", "read"),
        "tablePermissions": permissions,
    }


# --------------------------------------------------------------------------
# Folder reader
# --------------------------------------------------------------------------

def is_tmdl_model(path: Path) -> bool:
    """True if `path` is (or contains) a TMDL definition folder."""
    path = Path(path)
    if path.is_dir():
        if (path / "model.tmdl").exists() or any(path.glob("*.tmdl")):
            return True
        definition = path / "definition"
        if definition.is_dir() and (
            (definition / "model.tmdl").exists() or any(definition.glob("*.tmdl"))
        ):
            return True
    return False


def find_definition_dir(path: Path) -> Path | None:
    path = Path(path)
    if path.is_dir():
        if (path / "model.tmdl").exists() or any(path.glob("*.tmdl")):
            return path
        definition = path / "definition"
        if definition.is_dir():
            if (definition / "model.tmdl").exists() or any(definition.glob("*.tmdl")):
                return definition
    return None


def read_tmdl_model(path: str | Path) -> dict:
    """Read a TMDL folder and return a TMSL-shaped document."""
    definition = find_definition_dir(Path(path))
    if definition is None:
        raise FileNotFoundError(
            f"'{path}' does not contain a TMDL definition (no *.tmdl files found)."
        )

    tables: list[dict] = []
    relationships: list[dict] = []
    roles: list[dict] = []
    expressions: list[dict] = []
    model_props: dict = {}
    query_groups: list[dict] = []
    model_annotations: list[dict] = []
    model_name = None
    compatibility = None
    warnings: list[str] = []

    def nodes_of(file: Path) -> list[Node]:
        try:
            return parse_tmdl(_read_text(file))
        except Exception as exc:  # a single bad file must not sink the run
            warnings.append(f"Could not parse {file.name}: {exc}")
            return []

    # ---- model.tmdl / database.tmdl -------------------------------------
    for node in nodes_of(definition / "model.tmdl") if (definition / "model.tmdl").exists() else []:
        if node.keyword == "model":
            model_name = node.name or model_name
            model_props = dict(node.properties)
            # relationships and roles are sometimes declared inline here
            relationships += [_relationship_to_tmsl(r) for r in node.kids("relationship")]
            roles += [_role_to_tmsl(r) for r in node.kids("role")]
            for e in node.kids("expression"):
                expressions.append(_expression_to_tmsl(e))
            for t in node.kids("table"):
                tables.append(_table_to_tmsl(t))
            query_groups += [_query_group_to_tmsl(g) for g in node.kids("queryGroup")]
            model_annotations += _annotations(node)
        # Desktop writes the model's own children after the model block, at the start of a line.
        elif node.keyword == "queryGroup":
            query_groups.append(_query_group_to_tmsl(node))
        elif node.keyword == "annotation":
            model_annotations.append({"name": node.name, "value": node.text()})
        elif node.keyword == "expression":
            expressions.append(_expression_to_tmsl(node))

    db_file = definition.parent / "database.tmdl"
    if not db_file.exists():
        db_file = definition / "database.tmdl"
    if db_file.exists():
        for node in nodes_of(db_file):
            if node.keyword == "database":
                compatibility = node.prop("compatibilityLevel", compatibility)

    # ---- tables/ --------------------------------------------------------
    tables_dir = definition / "tables"
    table_files = sorted(tables_dir.glob("*.tmdl")) if tables_dir.is_dir() else []
    if not table_files:
        # some exports keep every table in the definition root
        table_files = [f for f in sorted(definition.glob("*.tmdl"))
                       if f.name not in ("model.tmdl", "relationships.tmdl",
                                         "database.tmdl", "expressions.tmdl",
                                         "cultures.tmdl", "roles.tmdl", "dataSources.tmdl")]
    for file in table_files:
        for node in nodes_of(file):
            if node.keyword == "table":
                tables.append(_table_to_tmsl(node))

    # ---- relationships --------------------------------------------------
    rel_files = [definition / "relationships.tmdl"]
    rel_dir = definition / "relationships"
    if rel_dir.is_dir():
        rel_files += sorted(rel_dir.glob("*.tmdl"))
    for file in rel_files:
        if not file.exists():
            continue
        for node in nodes_of(file):
            if node.keyword == "relationship":
                relationships.append(_relationship_to_tmsl(node))
            for child in node.kids("relationship"):
                relationships.append(_relationship_to_tmsl(child))

    # ---- roles ----------------------------------------------------------
    role_files = [definition / "roles.tmdl"]
    roles_dir = definition / "roles"
    if roles_dir.is_dir():
        role_files += sorted(roles_dir.glob("*.tmdl"))
    for file in role_files:
        if not file.exists():
            continue
        for node in nodes_of(file):
            if node.keyword == "role":
                roles.append(_role_to_tmsl(node))

    # ---- legacy provider data sources (pre-2019 PBIX models) --------------
    data_sources: list[dict] = []
    for file in [definition / "dataSources.tmdl"] + (sorted((definition / "dataSources").glob("*.tmdl"))
                                                    if (definition / "dataSources").is_dir() else []):
        if not file.exists():
            continue
        for node in nodes_of(file):
            if node.keyword == "dataSource":
                data_sources.append({"name": node.name, "type": node.value,
                                     **{k: v for k, v in node.properties.items()}})

    # ---- shared expressions / parameters --------------------------------
    expr_files = [definition / "expressions.tmdl"]
    expr_dir = definition / "expressions"
    if expr_dir.is_dir():
        expr_files += sorted(expr_dir.glob("*.tmdl"))
    for file in expr_files:
        if not file.exists():
            continue
        for node in nodes_of(file):
            if node.keyword == "expression":
                expressions.append(_expression_to_tmsl(node))

    # Desktop names the model object "Model"; the .SemanticModel folder name is
    # what the person actually calls this artifact, so prefer it.
    folder_name = re.sub(r"\.SemanticModel$", "", definition.parent.name, flags=re.I)
    if not model_name or model_name.lower() == "model":
        model_name = folder_name or model_name or "Model"

    doc: dict = {
        "name": model_name,
        "model": {
            "name": model_name,
            "culture": model_props.get("culture"),
            "defaultMode": model_props.get("defaultMode"),
            "tables": tables,
            "relationships": relationships,
            "roles": roles,
            "expressions": expressions,
            **({"queryGroups": query_groups} if query_groups else {}),
            **({"annotations": model_annotations} if model_annotations else {}),
            **({"dataSources": data_sources} if data_sources else {}),
        },
        "_sourceFormat": "TMDL",
        "_sourcePath": str(definition),
        "_readerWarnings": warnings,
    }
    if compatibility:
        try:
            doc["compatibilityLevel"] = int(compatibility)
        except (TypeError, ValueError):
            doc["compatibilityLevel"] = compatibility
    return doc
