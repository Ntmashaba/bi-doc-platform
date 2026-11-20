"""Shared small helpers for the SQL Agent engine."""

from __future__ import annotations

import re
from typing import Optional

_GUID_RE = re.compile(
    r"^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$"
)

_IDENT_PART = r"(?:\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_#@][A-Za-z0-9_@$#]*)"


def is_guid(text: str) -> bool:
    """True when *text* is a GUID literal (braces optional)."""
    return bool(_GUID_RE.match(text.strip()))


def split_sql_name(name: str, database: Optional[str] = None):
    """A harvested object name -> (server, database, schema, object).

    'T' and 'dbo.T' take the step's database; 'Sales.dbo.T' names its own database (a cross-database
    reference); 'SRV.Sales.dbo.T' adds a linked server. Parts the name leaves out are None.
    """
    parts = [strip_brackets(p) for p in _dotted_parts(name or "")]
    parts = [p for p in parts if p] if len(parts) <= 2 else parts
    if len(parts) >= 4:
        server, db, schema, obj = ".".join(parts[:-3]) or None, parts[-3] or None, parts[-2] or None, parts[-1]
        return server, db, schema, obj
    if len(parts) == 3:
        return None, parts[0] or None, parts[1] or None, parts[2]
    if len(parts) == 2:
        return None, database, parts[0], parts[1]
    return None, database, None, parts[0] if parts else name


def _dotted_parts(name: str):
    """Split on dots outside [brackets] and "quotes"; an empty part (Sales..T) is kept as ''."""
    parts, cur, close = [], [], None
    i = 0
    while i < len(name):
        c = name[i]
        if close:
            cur.append(c)
            if c == close:
                if i + 1 < len(name) and name[i + 1] == close:     # ]] or "" inside a delimited part
                    cur.append(close)
                    i += 1
                else:
                    close = None
        elif c in "[\"":
            close = "]" if c == "[" else '"'
            cur.append(c)
        elif c == ".":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


def strip_brackets(name: str) -> str:
    """Remove bracket/quote delimiters from a T-SQL identifier part."""
    n = name.strip()
    if len(n) >= 2 and n[0] == "[" and n[-1] == "]":
        return n[1:-1].replace("]]", "]")
    if len(n) >= 2 and n[0] == '"' and n[-1] == '"':
        return n[1:-1].replace('""', '"')
    return n


def norm_ident(name: Optional[str]) -> str:
    """Normalise an identifier for case-insensitive comparison."""
    return strip_brackets(name).strip().lower() if name else ""


def dotted_last(name: str) -> str:
    """Last part of a possibly dotted identifier (msdb.dbo.sp_add_job -> sp_add_job).

    Handles bracketed/quoted dotted names without splitting inside brackets.
    """
    parts = []
    buf = []
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if ch == "[":
            j = name.find("]", i + 1)
            j = n - 1 if j == -1 else j
            buf.append(name[i : j + 1])
            i = j + 1
        elif ch == '"':
            j = name.find('"', i + 1)
            j = n - 1 if j == -1 else j
            buf.append(name[i : j + 1])
            i = j + 1
        elif ch == ".":
            parts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    parts.append("".join(buf))
    return parts[-1] if parts else name


def truncate(text: str, limit: int = 200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"
