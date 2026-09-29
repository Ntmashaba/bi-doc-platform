"""Model-wide quality findings: documentation coverage and duplicate measures.

Static facts only. Duplicate detection compares DAX text after removing what
cannot change the result (whitespace, comments, letter case outside string
literals, quotes around simple table names); it does not evaluate DAX, so two
measures that are equivalent but written differently are not reported.
"""
from __future__ import annotations

import re
from collections import defaultdict

_SIMPLE_TABLE = re.compile(r"'([^\W\d]\w*)'(?=\s*\[)", re.UNICODE)


def normalise_dax(expression: str) -> str:
    text = expression or ""
    out, i, word = [], 0, False
    while i < len(text):
        ch = text[i]
        if ch == '"':  # string literal: kept exactly, including case
            j = i + 1
            while j < len(text):
                if text[j] == '"':
                    if text[j + 1:j + 2] == '"':
                        j += 2
                        continue
                    break
                j += 1
            out.append(text[i:j + 1])
            i, word = j + 1, False
            continue
        if text.startswith(("//", "--"), i):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = len(text) if j < 0 else j + 2
            continue
        if ch.isspace():
            # Keep one space only where it separates two word characters.
            j = i
            while j < len(text) and text[j].isspace():
                j += 1
            if word and j < len(text) and (text[j].isalnum() or text[j] == "_"):
                out.append(" ")
            i = j
            continue
        out.append(ch.upper())
        word = ch.isalnum() or ch == "_"
        i += 1
    return _SIMPLE_TABLE.sub(r"\1", "".join(out))


def duplicate_measures(measures: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for m in measures:
        key = normalise_dax(m.get("expression"))
        if key:
            groups[key].append(m)
    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        exact = len({(m.get("expression") or "").strip() for m in members}) == 1
        out.append(dict(
            measures=[f"{m['table']}[{m['name']}]" for m in members],
            expression=members[0].get("expression") or "",
            match="Identical DAX" if exact else "Same DAX apart from formatting, comments or letter case",
            formats=[m.get("formatString") or "" for m in members]))  # aligned with measures
    return sorted(out, key=lambda d: d["measures"])


def documentation_coverage(model: dict) -> dict:
    tables = [t for t in model.get("tables", []) if not t.get("isHidden")]
    columns = [c for t in tables for c in t.get("columns", []) if not c.get("isHidden")]
    measures = [m for m in model.get("measures", []) if not m.get("isHidden")]
    return {
        "measures": len(measures),
        "measuresDescribed": sum(1 for m in measures if m.get("description")),
        "measuresWithoutFormat": sorted(f"{m['table']}[{m['name']}]" for m in measures
                                        if not m.get("formatString") and not m.get("formatStringExpression")),
        "measuresInFolders": sum(1 for m in measures if m.get("displayFolder")),
        "columns": len(columns),
        "columnsDescribed": sum(1 for c in columns if c.get("description")),
        "tables": len(tables),
        "tablesDescribed": sum(1 for t in tables if t.get("description")),
        "scope": "Visible tables, columns and measures only; hidden objects are excluded.",
    }


def build_quality(model: dict | None) -> dict | None:
    if not model:
        return None
    return {"documentation": documentation_coverage(model),
            "duplicateMeasures": duplicate_measures(model.get("measures", []))}
