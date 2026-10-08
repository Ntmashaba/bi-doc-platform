"""Parse a semantic model into a normalized dictionary.

Accepts either format a Power BI project can use:

    model.bim                       TMSL, a single JSON document
    Model.bim / script.xmla         an Analysis Services tabular model: JSON at compatibility level 1200 and
                                    later (also inside an SSMS CREATE script), XML at 1100 and 1103
    Sales.SemanticModel/            a project folder holding either format
    Sales.SemanticModel/definition/ TMDL, a folder of .tmdl text files

TMDL is read by `tmdl_reader`, which emits the same TMSL shape, so everything
below this point is format-agnostic.

Handles TMSL quirks:
- expressions stored as either a string or a list of lines
- files saved as UTF-8, UTF-8 with BOM, or UTF-16
- optional / missing collections throughout

Everything here is static analysis. DAX and M are parsed with regular
expressions, which covers the overwhelming majority of real-world models but
cannot follow dynamically constructed references.
"""

from __future__ import annotations

import json
import re
from .data_sources import describe_all
from .dax_lexer import mask_dax, REFERENCE
from .legacy_mashup import is_mashup_source, location, member_descriptions, members, section_text
from .input_validation import validate_model
from pathlib import Path
from .source_inventory import enrich_source
from .source_labels import refine_source_type, source_label
from .partition_sources import apply_traced_sources


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be")


def load_json_lenient(path: Path) -> dict:
    """Load JSON trying several encodings (model.bim is often UTF-16)."""
    raw = path.read_bytes()
    last_err: Exception | None = None
    for enc in _ENCODINGS:
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_err = exc
    raise ValueError(f"Could not parse {path} as JSON in any known encoding: {last_err}")


# TMSL commands that carry a whole object definition, and the rest (which never do).
_TMSL_DEFINING = {"create": "create", "createorreplace": "createOrReplace", "alter": "alter"}   # as TMSL spells them
_TMSL_OTHER = ("refresh", "delete", "backup", "restore", "attach", "detach", "synchronize", "mergepartitions")


def _scripted_database(command) -> dict | None:
    """The database a single create / createOrReplace / alter command defines, if it defines a whole one."""
    if not isinstance(command, dict):
        return None
    for key, body in command.items():
        if str(key).lower() in _TMSL_DEFINING and isinstance(body, dict):
            database = body.get("database")
            if isinstance(database, dict) and isinstance(database.get("model"), dict):
                return database
    return None


def unwrap_tmsl_script(doc):
    """The database definition inside a TMSL command script, or `doc` itself when it is not a script.

    SSMS "Script Database as > CREATE To" writes a model at compatibility level 1200 or later as
    ``{"create": {"database": {...}}}`` (CREATE OR REPLACE and ALTER add an "object" beside it), usually in a
    file named .xmla. The database object inside is the same document as a model.bim. A ``sequence`` is searched
    for its first command that defines a database.
    """
    if not isinstance(doc, dict) or "model" in doc or "tables" in doc:
        return doc
    how = "In SSMS, right-click the database > Script > Script Database as > CREATE To."
    commands = {str(key).lower(): value for key, value in doc.items()}
    if "sequence" in commands:
        operations = commands["sequence"].get("operations") if isinstance(commands["sequence"], dict) else None
        for operation in operations if isinstance(operations, list) else []:
            database = _scripted_database(operation)
            if database is not None:
                return database
        raise ValueError("This TMSL sequence does not define a database. " + how)
    database = _scripted_database(doc)
    if database is not None:
        return database
    for name, spelled in _TMSL_DEFINING.items():
        if isinstance(commands.get(name), dict):
            raise ValueError(f"This TMSL '{spelled}' script does not define a whole database (it scripts a single "
                             "object). " + how)
    for name in _TMSL_OTHER:
        if name in commands and len(commands) == 1:
            raise ValueError(f"This is a TMSL '{name}' command, not a model definition. " + how)
    return doc


def load_model_file(path: Path) -> tuple[dict, str]:
    """(model.bim document, format name) for a model file: a model.bim, a TMSL script of a database, or the XML
    definition of a tabular model at compatibility level 1100 or 1103 (a Model.bim or an SSMS CREATE script)."""
    from .assl_reader import looks_like_xml, read_assl_model
    if looks_like_xml(path):
        return read_assl_model(path), "ASSL"
    doc = unwrap_tmsl_script(load_json_lenient(path))
    if isinstance(doc, dict) and "model" not in doc and "tables" not in doc:
        # Any other JSON object used to load as a model with no tables, and a document was written for it.
        raise ValueError(f"{path.name} is JSON but not a model definition: it has no \"model\" object. Expected a "
                         "model.bim, or a TMSL script that creates a database.")
    return doc, "TMSL"


def expr_text(value) -> str:
    """TMSL expressions can be a plain string or a list of lines."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value)


# --------------------------------------------------------------------------
# M / Power Query source extraction
# --------------------------------------------------------------------------

_M_PATTERNS = [
    # (source_type, regex, groups -> dict keys)
    ("SQL Server", re.compile(r'Sql\.Databases?\s*\(\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?', re.I), ("server", "database")),
    ("Azure Synapse / SQL", re.compile(r'AzureSql(?:Database)?\.Databases?\s*\(\s*"([^"]+)"', re.I), ("server",)),
    ("Databricks", re.compile(r'Databricks\.(?:Catalogs|Query|Contents)\s*\(\s*"([^"]+)"', re.I), ("server",)),
    ("Snowflake", re.compile(r'Snowflake\.Databases\s*\(\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?', re.I), ("server", "database")),
    ("Excel workbook", re.compile(r'Excel\.Workbook\s*\(\s*File\.Contents\s*\(\s*"([^"]+)"', re.I), ("path",)),
    ("CSV file", re.compile(r'Csv\.Document\s*\(\s*File\.Contents\s*\(\s*"([^"]+)"', re.I), ("path",)),
    ("SharePoint files", re.compile(r'SharePoint\.(?:Files|Contents)\s*\(\s*"([^"]+)"', re.I), ("url",)),
    ("SharePoint list", re.compile(r'SharePoint\.Tables\s*\(\s*"([^"]+)"', re.I), ("url",)),
    ("Web", re.compile(r'Web\.Contents\s*\(\s*"([^"]+)"', re.I), ("url",)),
    ("OData", re.compile(r'OData\.Feed\s*\(\s*"([^"]+)"', re.I), ("url",)),
    ("ODBC", re.compile(r'Odbc\.(?:DataSource|Query)\s*\(\s*"([^"]+)"', re.I), ("dsn",)),
    ("Power Platform dataflow", re.compile(r'PowerPlatform\.Dataflows', re.I), ()),
    ("Power BI dataflow", re.compile(r'PowerBI\.Dataflows', re.I), ()),
]

_ITEM_SCHEMA = re.compile(r'\[\s*(?:Schema\s*=\s*"([^"]+)"\s*,\s*)?Item\s*=\s*"([^"]+)"', re.I)
_NAME_NAV = re.compile(r'\{\s*\[\s*Name\s*=\s*"([^"]+)"', re.I)
_NATIVE_QUERY = re.compile(r'Value\.NativeQuery\s*\(', re.I)
_DATAFLOW_IDS = re.compile(
    r'(workspaceId|dataflowId|entity(?:Name)?)\s*=\s*"([^"]+)"', re.I
)
_CALENDAR = re.compile(r'\bCALENDAR(?:AUTO)?\s*\(', re.I)


def extract_m_source(expression: str, mode: str) -> dict:
    """Best-effort extraction of the upstream source from a partition."""
    src = {
        "sourceType": "Unknown",
        "server": None,
        "database": None,
        "schema": None,
        "object": None,
        "detail": None,
        "nativeQuery": bool(_NATIVE_QUERY.search(expression)),
    }
    if mode == "calculated" or (not expression.strip().lower().startswith("let") and _CALENDAR.search(expression)):
        src["sourceType"] = "Calculated (DAX)"
        if _CALENDAR.search(expression):
            src["detail"] = "Generated date table (CALENDAR/CALENDARAUTO)"
        return src

    for source_type, pattern, keys in _M_PATTERNS:
        m = pattern.search(expression)
        if m:
            src["sourceType"] = source_type
            for key, val in zip(keys, m.groups()):
                if val:
                    src[key if key in src else "detail"] = val
            break
    # Same vocabulary as the M tracer (external_sources) so views agree.
    src["sourceType"] = refine_source_type(src["sourceType"], src.get("detail"))

    schema_item = _ITEM_SCHEMA.search(expression)
    if schema_item:
        src["schema"] = schema_item.group(1)
        src["object"] = schema_item.group(2)
    else:
        navs = _NAME_NAV.findall(expression)
        if navs:
            # first navigation is usually the database, last is the object
            if src["database"] is None and len(navs) > 1:
                src["database"] = navs[0]
            src["object"] = navs[-1]

    if src["sourceType"] in ("Power Platform dataflow", "Power BI dataflow"):
        for key, val in _DATAFLOW_IDS.findall(expression):
            k = key.lower()
            if "workspace" in k:
                src["server"] = val
            elif "dataflow" in k:
                src["database"] = val
            else:
                src["object"] = val
    return src


# --------------------------------------------------------------------------
# DAX reference extraction
# --------------------------------------------------------------------------

# 'Table Name'[Column] or Table[Column]
_QUALIFIED_REF = re.compile(r"(?:'([^']+)'|([A-Za-z_][\w ]*?))\s*\[([^\[\]]+)\]")
# bare [Something] not preceded by a table token / quote / closing bracket
_BARE_REF = re.compile(r"(?<![\w'\]])\[([^\[\]]+)\]")
_DAX_COMMENT = re.compile(r"//[^\n]*|--[^\n]*|/\*.*?\*/", re.S)
_DAX_STRING = re.compile(r'"(?:[^"]|"")*"')


def _strip_dax(expression: str) -> str:
    return mask_dax(expression)


def extract_dax_refs(expression: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (qualified refs as (table, field), bare [refs])."""
    text = _strip_dax(expression or "")
    qualified, bare = [], []
    for match in REFERENCE.finditer(text):
        table = (match['quoted'] or match['table'] or '').replace("''", "'")
        field = match['field'].replace(']]', ']')
        if match['table'] and table.upper() in {'RETURN', 'NOT', 'IN', 'AND', 'OR'}:
            table = ''
        if table:
            qualified.append((table, field))
        else:
            bare.append(field)
    return qualified, bare


# --------------------------------------------------------------------------
# Main parse
# --------------------------------------------------------------------------

def load_model_document(model_path: str | Path) -> tuple[dict, str, Path]:
    """Resolve a model path to (TMSL-shaped document, format name, real path).

    Accepts a .bim file, a .SemanticModel folder containing either format, or a
    TMDL definition folder directly.
    """
    from .tmdl_reader import find_definition_dir, read_tmdl_model

    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(f"Semantic model not found: {path}")

    if path.is_file():
        if path.suffix.lower() in (".abf", ".pbix"):
            from .portable import model_document
            return model_document(path), "PBIXRay", path
        return (*load_model_file(path), path)

    # A folder: prefer an explicit model.bim, else look for TMDL.
    for candidate in (path / "model.bim", path / "definition" / "model.bim"):
        if candidate.is_file():
            return (*load_model_file(candidate), candidate)

    definition = find_definition_dir(path)
    if definition is not None:
        return read_tmdl_model(path), "TMDL", definition

    raise FileNotFoundError(
        f"'{path}' is not a semantic model: expected a model.bim or a "
        f"definition/ folder of .tmdl files inside it."
    )


def inline_legacy_mashups(model: dict) -> None:
    """Replace legacy placeholder partitions (SELECT * FROM [Age] against a
    Microsoft.PowerBI.OleDb data source) with the Power Query member they stand
    for, and expose the section's other members as shared expressions so
    references between queries can be traced."""
    sources = {d.get("name"): d for d in model.get("dataSources") or [] if is_mashup_source(d)}
    if not sources:
        return
    section, described = {}, {}
    for ds in sources.values():
        text = section_text(ds)
        described.update(member_descriptions(text))
        for name, expression in members(text).items():
            section.setdefault(name, expression)
    # The file's whole package, when the reader found it: it also holds the queries no table reads, and what
    # the file records about each query (its folder, what it returned).
    package = model.get("mashupPackage") if isinstance(model.get("mashupPackage"), dict) else {}
    described.update(member_descriptions(package.get("section")))
    for name, expression in members(package.get("section")).items():
        section.setdefault(name, expression)
    facts = package.get("queries") if isinstance(package.get("queries"), dict) else {}
    used = set()
    for tbl in model.get("tables") or []:
        for part in tbl.get("partitions") or []:
            src = part.get("source") or {}
            ds = sources.get(src.get("dataSource"))
            member = location(ds) if ds else None
            if (src.get("type") or ("query" if "query" in src else "")) == "query" and member in section:
                part["source"] = dict(src, type="m", expression=section[member], legacyQuery=src.get("query"))
                folder = (facts.get(member) or {}).get("queryGroup")
                if folder and not part.get("queryGroup"):
                    part["queryGroup"] = folder
                used.add(member)
    existing = {e.get("name") for e in model.get("expressions") or []}
    extra = []
    for name, expression in section.items():
        if name in used | existing:
            continue
        item = {"name": name, "kind": "m", "expression": expression, "legacyMashup": True}
        fact = facts.get(name) or {}
        if fact.get("queryGroup"):
            item["queryGroup"] = fact["queryGroup"]
        if fact.get("resultType"):
            item["annotations"] = [{"name": "PBI_ResultType", "value": fact["resultType"]}]
        if described.get(name):
            item["description"] = described[name]
        extra.append(item)
    if extra:
        model["expressions"] = list(model.get("expressions") or []) + extra
    if package.get("groups") and not model.get("queryGroups"):
        model["queryGroups"] = [{"folder": g["folder"], "description": g.get("description"),
                                 "annotations": [{"name": "PBI_QueryGroupOrder", "value": str(g["order"])}]
                                 if g.get("order") is not None else []}
                                for g in package["groups"] if isinstance(g, dict) and g.get("folder")]


CROSS_FILTER_ONE = "oneDirection"
CROSS_FILTER_BOTH = "bothDirections"
CROSS_FILTER_AUTOMATIC = "automatic"
_CROSS_FILTER_NAMES = {
    "onedirection": CROSS_FILTER_ONE,
    "singledirection": CROSS_FILTER_ONE,      # spelling used by some readers and older defaults; same meaning
    "bothdirections": CROSS_FILTER_BOTH,
    "automatic": CROSS_FILTER_AUTOMATIC,
}
_CROSS_FILTER_NUMBERS = {1: CROSS_FILTER_ONE, 2: CROSS_FILTER_BOTH, 3: CROSS_FILTER_AUTOMATIC}   # TOM enum values


def normalize_cross_filtering(value) -> str:
    """One canonical spelling of a relationship's cross-filter direction, whichever reader produced it.

    `oneDirection` (also spelled `singleDirection`, and what an absent property means), `bothDirections`, and `automatic`
    (the engine chooses the direction, so it is neither asserted single nor both) are the canonical values. Anything
    else is kept as written rather than guessed at, and is reported as unrecognised."""
    if value is None or value == "":
        return CROSS_FILTER_ONE
    if isinstance(value, int) and not isinstance(value, bool):
        return _CROSS_FILTER_NUMBERS.get(value, str(value))
    return _CROSS_FILTER_NAMES.get(str(value).strip().lower(), str(value))


def cross_filter_label(value) -> str:
    """The short word a document shows for a canonical cross-filter value."""
    value = normalize_cross_filtering(value)
    return {CROSS_FILTER_ONE: "single", CROSS_FILTER_BOTH: "both", CROSS_FILTER_AUTOMATIC: "automatic"}.get(value, value)


def parse_model(model_path: str | Path) -> dict:
    bim_path = Path(model_path)
    doc, source_format, bim_path = load_model_document(bim_path)
    validate_model(doc)
    model = doc.get("model", doc)
    inline_legacy_mashups(model)

    tables_out: list[dict] = []
    relationships_out: list[dict] = []
    roles_out: list[dict] = []
    warnings: list[dict] = []

    measure_index: dict[str, str] = {}  # measure name -> home table
    column_index: dict[tuple[str, str], dict] = {}

    # ---- tables ---------------------------------------------------------
    for tbl in model.get("tables", []):
        name = tbl.get("name", "")
        columns = []
        for col in tbl.get("columns", []):
            c = {
                "name": col.get("name", ""),
                "sourceColumn": col.get("sourceColumn"),
                "isKey": bool(col.get("isKey", False)),
                "dataType": col.get("dataType", ""),
                "isHidden": bool(col.get("isHidden", False)),
                "isCalculated": col.get("type") == "calculated",
                "expression": expr_text(col.get("expression")) or None,
                "sortByColumn": col.get("sortByColumn"),
                "description": expr_text(col.get("description")) or None,
                "dataCategory": col.get("dataCategory"),
                "formatString": expr_text(col.get("formatString")) or None,
                "displayFolder": expr_text(col.get("displayFolder")) or None,
                "lineageTag": col.get("lineageTag") or None,
            }
            columns.append(c)
            column_index[(name, c["name"])] = c

        measures = []
        for mea in tbl.get("measures", []):
            measures.append({
                "name": mea.get("name", ""),
                "expression": expr_text(mea.get("expression")),
                "displayFolder": expr_text(mea.get("displayFolder")) or None,
                "formatString": expr_text(mea.get("formatString")) or None,
                "description": expr_text(mea.get("description")) or None,
                "isHidden": bool(mea.get("isHidden", False)),
                "detailRowsDefinition": mea.get("detailRowsDefinition"),
                "formatStringExpression": expr_text((mea.get("formatStringDefinition") or {}).get("expression")),
                "table": name,
                "lineageTag": mea.get("lineageTag") or None,
            })
            measure_index[mea.get("name", "")] = name

        partitions = []
        for part in tbl.get("partitions", []):
            src = part.get("source", {}) or {}
            data_source = next((d for d in model.get("dataSources", [])
                                if d.get("name") == src.get("dataSource")), None)
            # A provider data source's partition is {"query": ..., "dataSource": ...}; Analysis Services
            # writes no "type" for it. Not for a pre-2019 Power BI mashup source: its query is a placeholder
            # (SELECT * FROM [Age]) for Power Query that `inline_legacy_mashups` could not find, so the source
            # stays unknown rather than being shown as SQL that was never run.
            untyped_sql = "query" in src and "expression" not in src and not is_mashup_source(data_source)
            p_mode = src.get("type") or ("query" if untyped_sql else "m")
            expression = expr_text(src.get("query", src.get("expression")) if p_mode == "query" else src.get("expression"))
            if p_mode == "entity":
                # Direct Lake / Fabric: the upstream object is named outright
                # rather than expressed in M.
                entity = src.get("entityName") or part.get("name", "")
                schema = src.get("schemaName")
                # An entity in DirectQuery mode reads through a shared expression
                # (DirectQuery to Analysis Services / a published model); only
                # Direct Lake mode is really Direct Lake. partition_sources
                # resolves the expression to its real source.
                direct_lake = str(part.get("mode", "")).lower() == "directlake" or not src.get("expressionSource")
                source = {
                    "sourceType": "Direct Lake (entity)" if direct_lake else "DirectQuery (entity)",
                    "expressionSource": src.get("expressionSource"),
                    "server": None,
                    "database": src.get("expressionSource"),
                    "schema": schema,
                    "object": f"{schema}.{entity}" if schema else entity,
                    "detail": None,
                    "nativeQuery": False,
                }
            else:
                source = extract_m_source(expression, p_mode)
            source = enrich_source(source, expression, p_mode, data_source)
            source["label"] = source_label(source)
            partition = {
                "name": part.get("name", ""),
                "mode": part.get("mode", "import"),
                "type": p_mode,
                "expression": expression,
                "source": source,
            }
            if part.get("queryGroup"):
                partition["queryGroup"] = str(part["queryGroup"])
            if p_mode == "query" and is_mashup_source(data_source):
                # A pre-2019 table whose Power Query member could not be read: the query exists, its text does not.
                partition["mashupLocation"] = location(data_source) or name
            partitions.append(partition)

        hierarchies = [
            {
                "name": h.get("name", ""),
                "levels": [lv.get("column") for lv in h.get("levels", [])],
                "levelDetails": [{"name": lv.get("name"), "column": lv.get("column")}
                                 for lv in h.get("levels", [])],
            }
            for h in tbl.get("hierarchies", [])
        ]

        annotations = {a.get("name"): a.get("value") for a in tbl.get("annotations", [])}

        calc_group = tbl.get("calculationGroup")
        tables_out.append({
            "name": name,
            "isHidden": bool(tbl.get("isHidden", False)),
            "calculationGroupDefinition": calc_group,
            "detailRowsDefinition": tbl.get("detailRowsDefinition"),
            "calculationGroup": (
                [{"name": ci.get("name", ""),
                  "expression": expr_text(ci.get("expression"))}
                 for ci in (calc_group.get("calculationItems") or [])]
                if calc_group else None
            ),
            "description": expr_text(tbl.get("description")) or None,
            "dataCategory": tbl.get("dataCategory"),
            "columns": columns,
            "measures": measures,
            "partitions": partitions,
            "hierarchies": hierarchies,
            "annotations": annotations,
            "lineageTag": tbl.get("lineageTag") or None,
        })

    table_names = {t["name"] for t in tables_out}

    # ---- relationships --------------------------------------------------
    for rel in model.get("relationships", []):
        relationships_out.append({
            "name": rel.get("name", ""),
            "fromTable": rel.get("fromTable", ""),
            "fromColumn": rel.get("fromColumn", ""),
            "toTable": rel.get("toTable", ""),
            "toColumn": rel.get("toColumn", ""),
            "isActive": rel.get("isActive", True),
            "crossFilteringBehavior": normalize_cross_filtering(rel.get("crossFilteringBehavior")),
            "fromCardinality": rel.get("fromCardinality", "many"),
            "toCardinality": rel.get("toCardinality", "one"),
        })

    # ---- roles ----------------------------------------------------------
    for role in model.get("roles", []):
        roles_out.append({
            "name": role.get("name", ""),
            "modelPermission": role.get("modelPermission", "read"),
            "tablePermissions": [
                {
                    "table": tp.get("name", ""),
                    "filterExpression": expr_text(tp.get("filterExpression")),
                }
                for tp in role.get("tablePermissions", [])
            ],
        })

    # ---- DAX dependency graph ------------------------------------------
    # measure name -> {"measures": set, "columns": set[(table, col)], "tables": set}
    measure_deps: dict[str, dict] = {}
    all_measure_names = set(measure_index)

    for tbl in tables_out:
        for mea in tbl["measures"]:
            qualified, bare = extract_dax_refs(mea["expression"])
            dep_measures, dep_columns, dep_tables = set(), set(), set()
            for table, field in qualified:
                if table in table_names:
                    if (table, field) in column_index:
                        dep_columns.add((table, field))
                        dep_tables.add(table)
                    elif field in all_measure_names:
                        dep_measures.add(field)
                    else:
                        dep_tables.add(table)  # table-level function ref
                elif field in all_measure_names:
                    dep_measures.add(field)
            for field in bare:
                if field in all_measure_names:
                    dep_measures.add(field)
            measure_deps[mea["name"]] = {
                "measures": dep_measures,
                "columns": dep_columns,
                "tables": dep_tables,
            }

    def resolve_tables(measure: str, seen: set[str]) -> set[str]:
        """All tables a measure ultimately touches (cycle-safe)."""
        if measure in seen or measure not in measure_deps:
            return set()
        seen.add(measure)
        deps = measure_deps[measure]
        tables = set(deps["tables"])
        for child in deps["measures"]:
            tables |= resolve_tables(child, seen)
        return tables

    measures_flat = []
    for tbl in tables_out:
        for mea in tbl["measures"]:
            deps = measure_deps.get(mea["name"], {"measures": set(), "columns": set(), "tables": set()})
            mea["dependsOnMeasures"] = sorted(deps["measures"])
            mea["dependsOnColumns"] = sorted(f"{t}[{c}]" for t, c in deps["columns"])
            mea["directTables"] = sorted(deps["tables"])
            mea["allTables"] = sorted(resolve_tables(mea["name"], set()))
            measures_flat.append(mea)

    # calculated column dependencies
    for tbl in tables_out:
        for col in tbl["columns"]:
            if col["isCalculated"] and col["expression"]:
                qualified, bare = extract_dax_refs(col["expression"])
                refs = sorted({f"{t}[{c}]" for t, c in qualified if t in table_names})
                refs += sorted({f"[{b}]" for b in bare if b in all_measure_names})
                col["dependsOn"] = refs
            else:
                col["dependsOn"] = []

    # ---- internal column references (for semantic-only "no internal refs")
    internally_referenced: set[tuple[str, str]] = set()
    for rel in relationships_out:
        internally_referenced.add((rel["fromTable"], rel["fromColumn"]))
        internally_referenced.add((rel["toTable"], rel["toColumn"]))
    for tbl in tables_out:
        for h in tbl["hierarchies"]:
            for lvl in h["levels"]:
                if lvl:
                    internally_referenced.add((tbl["name"], lvl))
        for col in tbl["columns"]:
            if col["sortByColumn"]:
                internally_referenced.add((tbl["name"], col["sortByColumn"]))
    for deps in measure_deps.values():
        internally_referenced |= deps["columns"]
    for tbl in tables_out:
        for col in tbl["columns"]:
            for ref in col["dependsOn"]:
                m = re.match(r"^(.*)\[(.*)\]$", ref)
                if m and m.group(1):
                    internally_referenced.add((m.group(1), m.group(2)))
    for role in roles_out:
        for tp in role["tablePermissions"]:
            qualified, _ = extract_dax_refs(tp["filterExpression"])
            internally_referenced |= {q for q in qualified}

    for tbl in tables_out:
        for col in tbl["columns"]:
            col["internallyReferenced"] = (tbl["name"], col["name"]) in internally_referenced

    # ---- table classification ------------------------------------------
    many_side = {}
    one_side = {}
    for rel in relationships_out:
        many_side[rel["fromTable"]] = many_side.get(rel["fromTable"], 0) + 1
        one_side[rel["toTable"]] = one_side.get(rel["toTable"], 0) + 1

    for tbl in tables_out:
        name = tbl["name"]
        n_cols = len(tbl["columns"])
        n_meas = len(tbl["measures"])
        anns = " ".join(f"{k}={v}" for k, v in tbl["annotations"].items())
        exprs = " ".join(p["expression"] for p in tbl["partitions"])
        col_exprs = " ".join(c["expression"] or "" for c in tbl["columns"])

        is_date = (
            tbl["dataCategory"] == "Time"
            or any(c.get("dataCategory") == "Time" for c in tbl["columns"])
            or bool(_CALENDAR.search(exprs))
        )
        is_field_param = "NAMEOF" in col_exprs.upper() or "ParameterMetadata" in anns
        is_measure_container = n_meas > 0 and n_cols <= 1 and not many_side.get(name) and not one_side.get(name)
        is_helper = n_meas == 0 and not many_side.get(name) and not one_side.get(name) and tbl["isHidden"]

        if tbl.get("calculationGroup"):
            t_type = "calculation group"
        elif is_field_param:
            t_type = "field parameter"
        elif is_date:
            t_type = "date dimension"
        elif is_measure_container:
            t_type = "measure container"
        elif is_helper:
            t_type = "helper"
        elif many_side.get(name, 0) > one_side.get(name, 0):
            t_type = "fact"
        elif one_side.get(name, 0) > 0:
            t_type = "dimension"
        elif many_side.get(name, 0) > 0:
            t_type = "fact"
        else:
            t_type = "disconnected"
        tbl["tableType"] = t_type

    # ---- quality warnings ----------------------------------------------
    for rel in relationships_out:
        if not rel["isActive"]:
            warnings.append({
                "severity": "info",
                "category": "Inactive relationship",
                "message": f"{rel['fromTable']}[{rel['fromColumn']}] → {rel['toTable']}[{rel['toColumn']}] is inactive; it only applies inside USERELATIONSHIP().",
            })
        if rel["crossFilteringBehavior"] == CROSS_FILTER_BOTH:
            warnings.append({
                "severity": "warning",
                "category": "Bidirectional filter",
                "message": f"{rel['fromTable']} ↔ {rel['toTable']} filters in both directions; check for ambiguity and performance impact.",
            })
        elif rel["crossFilteringBehavior"] == CROSS_FILTER_AUTOMATIC:
            warnings.append({
                "severity": "info",
                "category": "Automatic cross-filter",
                "message": f"{rel['fromTable']} ↔ {rel['toTable']} uses an automatic cross-filter direction; the engine chooses it, so this document does not state it as single or both.",
            })
        elif rel["crossFilteringBehavior"] != CROSS_FILTER_ONE:
            warnings.append({
                "severity": "info",
                "category": "Unrecognised cross-filter",
                "message": f"{rel['fromTable']} ↔ {rel['toTable']} has the cross-filter value '{rel['crossFilteringBehavior']}', which is not recognised; it is shown as written.",
            })
        if rel["fromCardinality"] == "many" and rel["toCardinality"] == "many":
            warnings.append({
                "severity": "warning",
                "category": "Many-to-many",
                "message": f"{rel['fromTable']} ↔ {rel['toTable']} is many-to-many.",
            })

    if not any(t["tableType"] == "date dimension" for t in tables_out):
        warnings.append({
            "severity": "warning",
            "category": "No date dimension",
            "message": "No date dimension detected; time intelligence functions may not behave as expected.",
        })

    connected = set()
    for rel in relationships_out:
        connected.add(rel["fromTable"])
        connected.add(rel["toTable"])
    for tbl in tables_out:
        if tbl["name"] not in connected and tbl["tableType"] not in ("measure container", "field parameter", "helper", "calculation group"):
            warnings.append({
                "severity": "info",
                "category": "Disconnected table",
                "message": f"'{tbl['name']}' has no relationships to any other table.",
            })

    for msg in doc.get("_readerWarnings", []):
        warnings.append({"severity": "warning", "category": "Unreadable definition",
                         "message": msg})

    result = {
        "name": model.get("name") or bim_path.stem,
        "extraction": doc.get("_extraction"),
        "dependencyExpressions": _dependency_expressions(model),
        "expressions": [_expression(e) for e in model.get("expressions", [])],
        "queryGroups": _query_groups(model),
        "queryOrder": _query_order(model),
        "sourceFormat": source_format,
        "sourcePath": str(bim_path),
        "compatibilityLevel": doc.get("compatibilityLevel"),
        "culture": model.get("culture"),
        "dataSources": describe_all(model),
        "tables": tables_out,
        "measures": measures_flat,
        "relationships": relationships_out,
        "roles": roles_out,
        "warnings": warnings,
    }
    # Replace per-partition regex guesses with the traced source where the
    # tracer knows more (shared queries, parameters, dataflows, entered data).
    apply_traced_sources(result)
    return result


def _annotation(obj: dict, name: str):
    for item in obj.get("annotations") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item.get("value")
    return None


def _expression(e: dict) -> dict:
    """A shared expression: its text, and the query facts the file records beside it."""
    out = {"name": e.get("name", ""), "kind": e.get("kind", "m"), "expression": expr_text(e.get("expression"))}
    for key in ("lineageTag", "queryGroup"):
        if e.get(key):
            out[key] = str(e[key])
    description = expr_text(e.get("description"))
    if description:
        out["description"] = description
    result_type = _annotation(e, "PBI_ResultType")
    if result_type:
        out["resultType"] = str(result_type)
    if e.get("legacyMashup"):
        out["legacyMashup"] = True
    return out


def _query_groups(model: dict) -> list[dict]:
    """Power Query folders as the file records them (TOM QueryGroup): folder path, description and position."""
    groups = []
    for group in model.get("queryGroups") or []:
        if not isinstance(group, dict) or not group.get("folder"):
            continue
        order = _annotation(group, "PBI_QueryGroupOrder")
        try:
            order = int(order)
        except (TypeError, ValueError):
            order = None
        groups.append({"folder": str(group["folder"]), "description": expr_text(group.get("description")) or None,
                       "order": order})
    return groups


def _query_order(model: dict) -> list[str] | None:
    """The order of the queries pane, which Power BI Desktop saves as a model annotation."""
    raw = _annotation(model, "PBI_QueryOrder")
    if not raw:
        return None
    try:
        order = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return [str(name) for name in order] if isinstance(order, list) else None


def _dependency_expressions(model: dict) -> list[dict]:
    """Retain additional DAX roots that can block column deletion."""
    out = []
    def walk(node, table, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("expression", "filterExpression") and isinstance(value, (str, list)):
                    out.append({"table": table, "label": path, "expression": expr_text(value)})
                elif key not in ("annotations", "extendedProperties"):
                    walk(value, table, path + "/" + key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, table, path + "/" + str(value.get("name", i) if isinstance(value, dict) else i))
    for tbl in model.get("tables", []):
        name = tbl.get("name", "")
        for key in ("calculationGroup", "detailRowsDefinition"):
            walk(tbl.get(key), name, f"{name}/{key}")
        for measure in tbl.get("measures", []):
            walk(measure.get("detailRowsDefinition"), name, f"Measure {name}[{measure.get('name', '')}]/detailRowsDefinition")
        for part in tbl.get("partitions", []):
            if (part.get("source") or {}).get("type") == "calculated":
                walk(part["source"], name, f"Calculated table {name}")
    return out
