"""Load candidate job script files from disk.

Sniffing rule (v4): a .sql file is a job script when the first 64 KB contain
`sp_add_job` (case-insensitive, outside of nothing fancier — the harvest is a
plain substring search; false positives simply produce zero jobs and land in
coverage). Non-matching .sql files go to skippedFiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

SNIFF_BYTES = 64 * 1024
SNIFF_NEEDLE = "sp_add_job"


@dataclass
class LoadedFile:
    path: Path
    text: str


@dataclass
class LoadResult:
    files: List[LoadedFile] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # paths, as given
    errors: List[str] = field(default_factory=list)


def is_job_script_head(head: bytes) -> bool:
    try:
        text = head.decode("utf-8-sig", errors="ignore").lower()
    except Exception:  # pragma: no cover - decode with ignore cannot fail
        return False
    return SNIFF_NEEDLE in text


def load_file(path: Path) -> Optional[LoadedFile]:
    """Load one file if it sniffs as a job script; return None otherwise."""
    with open(path, "rb") as fh:
        head = fh.read(SNIFF_BYTES)
    if not is_job_script_head(head):
        return None
    text = head.decode("utf-8-sig", errors="replace")
    size = path.stat().st_size
    if size > SNIFF_BYTES:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8-sig", errors="replace")
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
