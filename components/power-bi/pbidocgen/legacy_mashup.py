"""Legacy provider data sources (pre-2019 PBIX models).

Tables in these models have a "query" partition such as ``SELECT * FROM [Age]``
against a data source whose connection string is
``Provider=Microsoft.PowerBI.OleDb;...;Mashup=<base64 package>;Location=Age``.
The SQL is a placeholder; the real source is the Power Query member ``Age`` in the
package's Formulas/Section1.m. pbi-tools' folder layout writes that file out as
dataSources/<name>/mashup/Formulas/Section1.m instead.
"""
from __future__ import annotations

import base64
import binascii
import io
import re
import zipfile

_KEY = re.compile(r'(?:^|;)\s*([^=;]+)=\s*("[^"]*"|[^;]*)')
_MEMBER = re.compile(r'\bshared\s+(#"(?:[^"]|"")*"|[A-Za-z_][\w.]*)\s*=', re.S)
MAX_PACKAGE = 50 * 1024 * 1024


def _connection(ds: dict) -> dict:
    text = (ds or {}).get("connectionString") or ""
    return {k.strip().lower(): v.strip().strip('"') for k, v in _KEY.findall(text)}


def is_mashup_source(ds: dict | None) -> bool:
    return "microsoft.powerbi.oledb" in (_connection(ds).get("provider") or "").lower()


def location(ds: dict | None) -> str | None:
    return _connection(ds).get("location") or None


def section_text(ds: dict | None) -> str | None:
    """Section1.m from the data source: pre-read by the folder loader, or decoded from Mashup=."""
    if not ds:
        return None
    if isinstance(ds.get("mashupSection"), str):
        return ds["mashupSection"]
    packed = _connection(ds).get("mashup")
    if not packed:
        return None
    try:
        raw = base64.b64decode(packed, validate=False)
    except (binascii.Error, ValueError):
        return None
    start = raw.find(b"PK\x03\x04")  # the package has a small binary header before its zip
    if start < 0:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw[start:])) as archive:
            name = next((n for n in archive.namelist() if n.replace("\\", "/").lower() == "formulas/section1.m"), None)
            if not name or archive.getinfo(name).file_size > MAX_PACKAGE:
                return None
            return archive.read(name).decode("utf-8-sig", errors="replace")
    except (zipfile.BadZipFile, OSError, ValueError):
        return None


def _name(token: str) -> str:
    return token[2:-1].replace('""', '"') if token.startswith('#"') else token


def _end(text: str, i: int) -> int:
    """Index of the ';' ending the member expression starting at i (skips strings/comments)."""
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    break
                i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 1
        elif c == ";":
            return i
        i += 1
    return n


def members(section: str | None) -> dict[str, str]:
    """{query name: M expression} for every shared member of a section document."""
    out = {}
    if not section:
        return out
    pos = 0
    while True:
        match = _MEMBER.search(section, pos)
        if not match:
            return out
        end = _end(section, match.end())
        out.setdefault(_name(match.group(1)), section[match.end():end].strip())
        pos = end + 1
