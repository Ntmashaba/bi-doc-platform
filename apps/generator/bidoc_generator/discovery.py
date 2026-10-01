"""Folder discovery: turn what a person selected into the list of inputs a batch will queue.

`bidoc batch`, the desktop review and the desktop's submission all call `discover`, so they see the same inputs.

A selection is one of:

* a **file**: classified exactly as before, whatever its type (an explicit choice is never second-guessed);
* a **recognised project folder** (PBIP, TMDL, PBIR, a pbi-tools extract, a Data Factory Git folder): one input, and its
  contents are never searched, so a model's own files or a factory's individual JSON files are not queued separately;
* any **other folder**: a container, searched recursively for `.pbix` and `.abf` files, `.bim` models and recognised project
  folders. Other files are ignored (a stray `.json` is not assumed to be a Data Factory export).

Searching does not enter the output folder or the generator's own folder, development folders (`.git`, `.venv`, ...), or any
symbolic link or Windows junction, and it is bounded. A folder that cannot be read, an empty scan and a scan that hit a bound
each become a *failed* item with a message, so they show up in the history and the exit code, while every readable input still
runs. Results are sorted, de-duplicated (selecting a folder and a file inside it queues the file once) and labelled with the
path below the selected folder's parent, so two `Sales.pbix` files in different folders can be told apart.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .batch import classify, classify_folder

FILE_SUFFIXES = {".pbix", ".abf", ".bim"}
SKIP_FOLDER_NAMES = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode",
                     "$recycle.bin", "system volume information"}
MAX_ITEMS = 1000            # discovered inputs per scan
MAX_FOLDERS = 20000         # folders visited per scan
_REPARSE_POINT = 0x400      # Windows: junctions and other reparse points


@dataclass
class Discovery:
    items: list[dict] = field(default_factory=list)       # classify()-shaped; failed ones carry "errors"
    warnings: list[str] = field(default_factory=list)     # worth showing, not a failure (links skipped, same file names)
    selections: int = 0
    folders_scanned: int = 0

    @property
    def usable(self) -> int:
        return sum(1 for i in self.items if not i.get("errors"))

    def as_dict(self) -> dict:
        return {"selections": self.selections, "found": self.usable, "problems": len(self.items) - self.usable,
                "folders_scanned": self.folders_scanned, "warnings": list(self.warnings)}


def _key(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _is_link(entry: os.DirEntry) -> bool:
    """A symbolic link, or on Windows a junction / reparse point: never followed."""
    try:
        if entry.is_symlink():
            return True
        junction = getattr(entry, "is_junction", None)          # Python 3.12+
        if junction is not None and junction():
            return True
        if os.name == "nt":
            return bool(entry.stat(follow_symlinks=False).st_file_attributes & _REPARSE_POINT)
    except OSError:
        return True                                             # cannot tell: do not follow
    return False


def _inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def _label(path: Path, root: Path) -> str:
    """The path below the selected folder's parent, with forward slashes: `Reports/Finance/Sales.pbix`."""
    try:
        return path.relative_to(root.parent).as_posix()
    except ValueError:
        return path.name


def _problem(path: Path, label: str, message: str) -> dict:
    return {"source": str(path), "label": label, "errors": [{"code": "INVALID_INPUT", "message": message}]}


def discover(paths, *, exclude=(), max_items: int = MAX_ITEMS, max_folders: int = MAX_FOLDERS) -> Discovery:
    """The inputs for `paths` (files and folders), per the rules in this module's docstring."""
    result = Discovery()
    skipped = [_key(p) for p in exclude if p]
    seen: set[str] = set()
    stems: dict[str, list[str]] = {}
    bucket = result.items                  # selections keep the order they were given; a folder's finds are sorted

    def add(item: dict) -> bool:
        key = _key(item["source"])
        if key in seen:
            return False
        seen.add(key)
        bucket.append(item)
        if not item.get("errors"):
            stems.setdefault(Path(item["source"]).stem.casefold(), []).append(item["label"])
        return True

    for raw in paths:
        result.selections += 1
        path = Path(raw)
        if not path.is_dir():
            add(classify(path))                                   # a file, or a path that is not there: as before
            continue
        project = classify_folder(path)
        if project is not None:                                   # a project (or a broken one) is one input
            add(project)
            continue
        bucket = []
        matched, problems = _scan(path, result, add, skipped, max_items, max_folders)
        if matched == 0 and problems == 0:
            add(_problem(path, path.name, f"no supported inputs found in {path} (looked for .pbix, .abf and .bim files "
                                          "and project folders, in this folder and below)"))
        bucket.sort(key=lambda i: i["label"].casefold())
        result.items.extend(bucket)
        bucket = result.items

    duplicates = sorted(s for s, labels in stems.items() if len(labels) > 1)
    if duplicates:
        result.warnings.append(
            "Several inputs share a file name (" + ", ".join(duplicates) + "). Their documents are told apart by identity, "
            "but a document that cannot be published is named after the file, so one may overwrite another.")
    return result


def _scan(root: Path, result: Discovery, add, skipped: list[str], max_items: int, max_folders: int) -> tuple[int, int]:
    """Search `root`, a container folder. Returns (inputs matched, problems recorded); a re-met input still counts as matched."""
    matched = problems = 0
    stack = [root]
    while stack:
        folder = stack.pop()
        if result.folders_scanned >= max_folders:
            add(_problem(folder, _label(folder, root), f"scan stopped after {max_folders} folders; select a smaller folder"))
            return matched, problems + 1
        if matched >= max_items:
            add(_problem(folder, _label(folder, root), f"scan stopped after {max_items} inputs; select a smaller folder"))
            return matched, problems + 1
        result.folders_scanned += 1
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.casefold())
        except OSError as exc:
            add(_problem(folder, _label(folder, root), f"cannot read folder {folder}: {exc.strerror or exc}"))
            problems += 1
            continue
        subfolders = []
        for entry in entries:
            child = folder / entry.name
            link = _is_link(entry)
            try:
                is_dir = entry.is_dir()                           # follows links, so a link to a folder is seen as a folder
            except OSError:
                is_dir = False
            if is_dir:
                if link:
                    result.warnings.append(f"Not followed (link or junction): {child}")
                elif entry.name.casefold() not in SKIP_FOLDER_NAMES and not any(_inside(_key(child), s) for s in skipped):
                    subfolders.append(child)
                continue
            if entry.name.startswith(("._", "~$")) or child.suffix.lower() not in FILE_SUFFIXES:
                continue
            if matched >= max_items:
                add(_problem(folder, _label(folder, root), f"scan stopped after {max_items} inputs; select a smaller folder"))
                return matched, problems + 1
            item = classify(child)
            item["label"] = _label(child, root)
            matched += 1
            add(item)
        for child in reversed(subfolders):                        # depth first, in name order
            project = classify_folder(child)
            if project is None:
                stack.append(child)
                continue
            project["label"] = _label(child, root)
            if project.get("errors"):
                problems += 1
            else:
                matched += 1
            add(project)
    return matched, problems
