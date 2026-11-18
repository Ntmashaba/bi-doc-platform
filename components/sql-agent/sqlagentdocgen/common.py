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
