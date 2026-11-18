"""Load candidate job script files from disk.

Sniffing rule (v4, widened after real-world validation): a .sql file is a job
script when the first 64 KB contain `sp_add_job`, `sp_add_schedule`, or
`sp_attach_schedule` (case-insensitive plain substring search; false positives
simply produce zero jobs and land in coverage). Non-matching .sql files go to
skippedFiles. The wider needles let shared-schedule-only scripts participate:
they carry schedule definitions other files attach to.

Encoding: SSMS has saved scripts as UTF-16 ("Unicode") for decades, so the
loader detects UTF-16 LE/BE (via BOM, or a NUL-byte heuristic when the BOM is
missing) and UTF-8 (with or without BOM) before sniffing.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

SNIFF_BYTES = 64 * 1024
SNIFF_NEEDLES = ("sp_add_job", "sp_add_schedule", "sp_attach_schedule")


@dataclass
class LoadedFile:
    path: Path
    text: str


@dataclass
class LoadResult:
    files: List[LoadedFile] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # paths, as given
    errors: List[str] = field(default_factory=list)


def _detect_encoding(head: bytes) -> str:
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"  # consumes the BOM
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    # UTF-16 without a BOM: mostly-ASCII text is NUL-interleaved on one side.
    sample = head[:512]
    odd = sample[1::2]
    even = sample[0::2]
    if odd and odd.count(0) * 3 > len(odd):
        return "utf-16-le"
    if even and even.count(0) * 3 > len(even):
        return "utf-16-be"
    return "utf-8-sig"


def _decode(data: bytes) -> str:
    return data.decode(_detect_encoding(data), errors="replace")


def is_job_script_head(head: bytes) -> bool:
    text = _decode(head).lower()
    return any(needle in text for needle in SNIFF_NEEDLES)


def load_file(path: Path) -> Optional[LoadedFile]:
    """Load one file if it sniffs as a job script; return None otherwise."""
    with open(path, "rb") as fh:
        head = fh.read(SNIFF_BYTES)
    if not is_job_script_head(head):
        return None
    size = path.stat().st_size
    if size > SNIFF_BYTES:
        with open(path, "rb") as fh:
            text = _decode(fh.read())
    else:
        text = _decode(head)
    return LoadedFile(path=path, text=text)


def load_path(target: Path) -> LoadResult:
    """Load a single .sql file or every .sql file in a folder (recursively)."""
    result = LoadResult()
    if target.is_file():
        try:
            loaded = load_file(target)
        except OSError as exc:
            result.errors.append(f"{target}: {exc}")
            return result
        if loaded is None:
            result.skipped.append(str(target))
        else:
            result.files.append(loaded)
        return result
    if target.is_dir():
        for path in sorted(target.rglob("*.sql")):
            try:
                loaded = load_file(path)
            except OSError as exc:
                result.errors.append(f"{path}: {exc}")
                continue
            if loaded is None:
                result.skipped.append(str(path))
            else:
                result.files.append(loaded)
        return result
    result.errors.append(f"{target}: not a file or directory")
    return result
