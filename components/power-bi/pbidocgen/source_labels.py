"""One vocabulary for source types and one label format, shared by every view.

Partition parsing (model_parser) and M tracing (external_sources) find the
same sources by different routes. Both pass their result through
``refine_source_type`` so Overview, Lineage and Sources name a source the
same way, and ``source_label`` gives the "type · name" text shown on screen.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

_NAMED_FILES = {".json": "JSON file", ".xml": "XML file", ".parquet": "Parquet file", ".pdf": "PDF file",
                ".accdb": "Access database", ".mdb": "Access database"}
_EXCEL = (".xlsx", ".xlsm", ".xlsb", ".xls")
_TEXT = (".csv", ".txt", ".tsv")


def _host(location: str) -> str:
    try:
        return urlsplit(location).netloc.rsplit("@", 1)[-1].lower() if re.match(r"^https?://", location or "", re.I) else ""
    except ValueError:
        return ""


def _extension(location: str) -> str:
    path = urlsplit(location).path if _host(location) else (location or "")
    return ("." + path.rsplit(".", 1)[-1].lower()) if "." in path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] else ""


def is_sharepoint(location: str) -> bool:
    host = _host(location)
    return host.endswith(".sharepoint.com") or ".sharepoint." in host


def refine_source_type(source_type: str, location: str | None) -> str:
    """Canonical type for a single-location source.

    Web.Contents on a SharePoint document is a SharePoint file, not an API,
    and File.Contents is named after the file it reads.
    """
    location = location or ""
    if source_type in ("Web", "Web / API") and is_sharepoint(location) and _extension(location):
        return "SharePoint file"
    if source_type == "Web":
        return "Web / API"
    if source_type == "SharePoint files" and is_sharepoint(location) and _extension(location):
        return "SharePoint file"  # one document picked from a library
    if source_type in ("File", "Folder"):
        # A folder navigated to one file is that file; a bare folder stays a folder.
        ext = _extension(location)
        if source_type == "Folder" and not ext:
            return source_type
        if ext in _EXCEL:
            return "Excel workbook"
        if ext in _TEXT:
            return "CSV file"
        if ext in _NAMED_FILES:
            return _NAMED_FILES[ext]
        return "File"
    return source_type


def file_name(location: str) -> str:
    if not location:
        return ""
    path = unquote(urlsplit(location).path) if _host(location) else location
    return re.split(r"[/\\]", path.rstrip("/\\"))[-1]


FILE_TYPES = {"SharePoint file", "Excel workbook", "CSV file", "File"}
# Cloud storage addressed by URL: name a single file by its file name.
STORAGE_TYPES = {"Azure Blob Storage", "Azure Data Lake"}
DATAFLOW_TYPES = {"Power BI dataflow", "Power Platform dataflow"}


def source_name(src: dict) -> str:
    """Short, recognisable name: the file for files, else server / database."""
    source_type = src.get("sourceType") or ""
    location = src.get("location") or src.get("detail") or src.get("server") or ""
    if source_type in FILE_TYPES:
        return file_name(location)
    if source_type in STORAGE_TYPES and _extension(location):
        return file_name(location)
    if source_type in DATAFLOW_TYPES:
        return src.get("object") or src.get("database") or ""  # entity names beat workspace GUIDs
    server, database = src.get("server") or "", src.get("database") or ""
    if server or database:
        return " / ".join(x for x in (server, database) if x)
    return location if _host(location) or location.startswith("\\\\") else ""


def source_label(src: dict) -> str:
    name = source_name(src)
    source_type = src.get("sourceType") or "Unknown"
    return f"{source_type} · {name}" if name else source_type
