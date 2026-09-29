"""Display names of custom visuals, from CustomVisuals/<id>/package.json.

A report stores a custom visual's type as its package id (PBI_CV_885EF3C3_...,
ClusterMap1652434605854); the package's visual.displayName is what people see in
Power BI (Image, Cluster Map). Both PBIX files and PBIP report folders carry it.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

_VERSION = re.compile(r"\s+v?\d+(\.\d+){1,3}$")
MAX_PACKAGE = 1024 * 1024


def _name(data) -> str | None:
    visual = data.get("visual") if isinstance(data, dict) else None
    name = (visual or {}).get("displayName") or (visual or {}).get("name")
    return _VERSION.sub("", name.strip()) if isinstance(name, str) and name.strip() else None


def _decode(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None


def from_folder(report_root: str | Path) -> dict[str, str]:
    """{package id: display name} from a report folder's CustomVisuals/."""
    folder = Path(report_root) / "CustomVisuals"
    names = {}
    if folder.is_dir():
        for package in folder.glob("*/package.json"):
            if package.stat().st_size <= MAX_PACKAGE:
                name = _name(_decode(package.read_bytes()))
                if name:
                    names[package.parent.name] = name
    return names


def from_pbix(pbix: str | Path) -> dict[str, str]:
    """{package id: display name} from Report/CustomVisuals/ inside a PBIX."""
    names = {}
    try:
        with zipfile.ZipFile(pbix) as archive:
            for info in archive.infolist():
                parts = info.filename.replace("\\", "/").split("/")
                if (len(parts) == 4 and parts[0] == "Report" and parts[1] == "CustomVisuals"
                        and parts[3] == "package.json" and info.file_size <= MAX_PACKAGE):
                    name = _name(_decode(archive.read(info)))
                    if name:
                        names[parts[2]] = name
    except (OSError, zipfile.BadZipFile):
        return {}
    return names
