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
import json
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

_KEY = re.compile(r'(?:^|;)\s*([^=;]+)=\s*("[^"]*"|[^;]*)')
# A member name is any M identifier: letters of every script, not only A-Z (shared Übersicht = ...).
_MEMBER = re.compile(r'\bshared\s+(#"(?:[^"]|"")*"|[^\W\d][\w.]*)\s*=', re.S)
# [ Description = "..." ] written before a member: the description typed in the query's properties.
_DESCRIBED = re.compile(r'\[\s*Description\s*=\s*"((?:[^"]|"")*)"\s*\]\s*shared\s+(#"(?:[^"]|"")*"|[^\W\d][\w.]*)\s*=', re.S)
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


def member_descriptions(section: str | None) -> dict[str, str]:
    """{query name: description} for members that carry a [Description = "..."] attribute."""
    return {_name(m.group(2)): m.group(1).replace('""', '"') for m in _DESCRIBED.finditer(section or "")}


# --------------------------------------------------------------------------------------------------------------
# The whole Power Query package of a pre-2019 file
#
# A table's data source holds a copy of the package with the table's own query and the queries it reads. The
# file's complete list of queries, including the ones no table reads, is in one place only: the DataMashup part
# of the PBIX, which pbi-tools writes out as the Mashup/ folder of an extract. Without it, a query that is not
# loaded and not read by a loaded query is missing from the documentation.
# --------------------------------------------------------------------------------------------------------------

def _section_from_package(package: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            name = next((n for n in archive.namelist() if n.replace("\\", "/").lower() == "formulas/section1.m"), None)
            if not name or archive.getinfo(name).file_size > MAX_PACKAGE:
                return None
            return archive.read(name).decode("utf-8-sig", errors="replace")
    except (zipfile.BadZipFile, OSError, ValueError):
        return None


def _entry(value):
    """A metadata entry value: a letter for its type, then the value ("l0", "sTable")."""
    if not isinstance(value, str) or not value:
        return value
    kind, rest = value[0], value[1:]
    if kind == "l":
        try:
            return int(rest)
        except ValueError:
            return rest
    return rest if kind in "sdfb" else value


def _folders(raw) -> tuple[list[dict], dict]:
    """(query folders, {folder id: path}) from the package's QueryGroups entry, when it is in the known form."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return [], {}
    if not isinstance(raw, list) or not all(isinstance(g, dict) and g.get("Id") and g.get("Name") for g in raw):
        return [], {}
    by_id = {str(g["Id"]): g for g in raw}

    def path(group, seen=()):
        parent = by_id.get(str(group.get("ParentId"))) if group.get("ParentId") else None
        if parent is None or str(group["Id"]) in seen:
            return str(group["Name"])
        return path(parent, seen + (str(group["Id"]),)) + "\\" + str(group["Name"])
    paths = {gid: path(g) for gid, g in by_id.items()}
    groups = [{"folder": paths[str(g["Id"])], "description": g.get("Description") or None,
               "order": g.get("Order") if isinstance(g.get("Order"), int) else None} for g in raw]
    return groups, paths


def _facts(formulas: dict, all_formulas: dict) -> dict:
    """What the package metadata records per query: {"queries": {name: {...}}, "groups": [...]}."""
    groups, paths = _folders(all_formulas.get("QueryGroups"))
    queries = {}
    for path_, entries in formulas.items():
        if not isinstance(entries, dict) or "/" not in path_:
            continue
        name = path_.split("/", 1)[1]
        if "/" in name:                                   # Section1/Query/Step: a step, not a query
            continue
        fact = {}
        if isinstance(entries.get("ResultType"), str):
            fact["resultType"] = entries["ResultType"]
        folder = paths.get(str(entries.get("QueryGroupID")))
        if folder:
            fact["queryGroup"] = folder
        queries[name] = fact
    return {"queries": queries, "groups": groups}


def read_data_mashup(raw: bytes) -> dict | None:
    """{"section", "queries", "groups"} from a DataMashup stream.

    Layout ([MS-QDEFF] 2.2): a version, then four parts each preceded by its length: the package (a zip holding
    Formulas/Section1.m), permissions, metadata (XML after its own version and length) and permission bindings.
    Only the package and the metadata are read. A stream in any other form gives None."""
    if len(raw) < 12 or len(raw) > MAX_PACKAGE:
        return None
    version, size = struct.unpack_from("<II", raw, 0)
    if version != 0 or size <= 0 or 8 + size > len(raw):
        return None
    section = _section_from_package(raw[8:8 + size])
    if section is None:
        return None
    out = {"section": section, "queries": {}, "groups": []}
    try:
        at = 8 + size
        (permissions,) = struct.unpack_from("<I", raw, at)
        at += 4 + permissions
        (metadata_size,) = struct.unpack_from("<I", raw, at)
        metadata = raw[at + 4:at + 4 + metadata_size]
        _, xml_size = struct.unpack_from("<II", metadata, 0)
        text = metadata[8:8 + xml_size].decode("utf-8-sig", errors="replace")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
            return out
        root = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("\ufeff")))
    except (struct.error, ET.ParseError, ValueError):
        return out
    formulas, all_formulas = {}, {}
    for item in root.iter("Item"):
        kind = (item.findtext("ItemLocation/ItemType") or "").strip()
        path_ = (item.findtext("ItemLocation/ItemPath") or "").strip()
        entries = {e.get("Type"): _entry(e.get("Value")) for e in item.iter("Entry") if e.get("Type")}
        if kind == "AllFormulas":
            all_formulas = entries
        elif kind == "Formula" and path_:
            formulas[path_] = entries
    out.update(_facts(formulas, all_formulas))
    return out


def pbix_package(path) -> dict | None:
    """The Power Query package inside a PBIX (its DataMashup part), or None when the file has none."""
    try:
        with zipfile.ZipFile(path) as archive:
            name = next((n for n in archive.namelist() if n.strip("/").lower() == "datamashup"), None)
            if not name or archive.getinfo(name).file_size > MAX_PACKAGE:
                return None
            return read_data_mashup(archive.read(name))
    except (zipfile.BadZipFile, OSError, ValueError):
        return None


def folder_package(extract) -> dict | None:
    """The package as pbi-tools writes it into an extract: Mashup/Package/Formulas/Section1.m, a file or a folder
    of one .m file per query, with Mashup/Metadata/metadata.json beside it."""
    root = Path(extract) / "Mashup"
    formulas = root / "Package" / "Formulas" / "Section1.m"
    try:
        if formulas.is_file():
            section = formulas.read_text(encoding="utf-8-sig", errors="replace")
        elif formulas.is_dir():
            parts = [p.read_text(encoding="utf-8-sig", errors="replace") for p in sorted(formulas.glob("*.m"))
                     if p.is_file() and p.stat().st_size <= MAX_PACKAGE]
            section = "section Section1;\n\n" + "\n\n".join(parts) if parts else None
        else:
            return None
    except OSError:
        return None
    if section is None:
        return None
    out = {"section": section, "queries": {}, "groups": []}
    metadata = root / "Metadata" / "metadata.json"
    try:
        if metadata.is_file() and metadata.stat().st_size <= MAX_PACKAGE:
            doc = json.loads(metadata.read_text(encoding="utf-8-sig"))
            if isinstance(doc, dict):
                out.update(_facts(doc.get("Formulas") if isinstance(doc.get("Formulas"), dict) else {},
                                  doc.get("AllFormulas") if isinstance(doc.get("AllFormulas"), dict) else {}))
    except (OSError, ValueError):
        pass
    return out
