"""Folder discovery: turn what a person selected into the list of inputs a batch will queue.

`bidoc batch`, the desktop review and the desktop's submission all call `discover`, so they see the same inputs.

A selection is one of:

* a **file**: classified exactly as before, whatever its type (an explicit choice is never second-guessed);
* a **recognised project folder** (PBIP, TMDL, PBIR, a pbi-tools extract, a Data Factory Git folder): one input, and its
  contents are never searched, so a model's own files or a factory's individual JSON files are not queued separately;
* any **other folder**: a container, searched recursively for `.pbix` and `.abf` files, `.bim` models and recognised project
  folders. Other files are ignored (a stray `.json` is not assumed to be a Data Factory export).

Rules that apply to every path, selected or found:

* **Links are never followed.** A symbolic link or Windows junction, as a selection, a folder or a file, is reported and left
  out (a selected one is a failed item that says so). Cloud-sync placeholders (OneDrive and the like) are not links.
* **The output folder and the generator's own folder are left out**, as a selection and inside a search; so are development
  folders (`.git`, `.venv`, ...).
* **Paths are normalised here**, once, so the command line, the desktop review and the desktop submission name the same file the
  same way (Windows short names such as `RUNNER~1` and relative paths included). Callers pass paths as given.
* **The limits cover the whole call**: at most `MAX_ITEMS` inputs and `MAX_FOLDERS` folder listings in total across every
  selection, counting each listing made to recognise a project. At the limit discovery stops, keeps what it found and records one
  failed item saying so.

A folder that cannot be read, an empty scan and a stopped scan are *failed* items with a message, so they show up in the history
and the exit code while every readable input still runs. Results keep the order of the selections; a folder's finds are sorted;
the same file reached twice is queued once; labels are the path below the selected folder's parent, so two `Sales.pbix` files
in different folders can be told apart.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .batch import classify, classify_folder

FILE_SUFFIXES = {".pbix", ".abf", ".bim"}
SKIP_FOLDER_NAMES = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode",
                     "$recycle.bin", "system volume information"}
MAX_ITEMS = 1000            # inputs and problems recorded, in total
MAX_FOLDERS = 20000         # folder listings, in total
_LINK_TAGS = {0xA000000C, 0xA0000003}   # Windows reparse tags: symbolic link and mount point (junction). Cloud files are others.


@dataclass
class Discovery:
    items: list[dict] = field(default_factory=list)       # classify()-shaped; failed ones carry "errors"
    warnings: list[str] = field(default_factory=list)     # worth showing, not a failure (links skipped)
    selections: int = 0
    folders_scanned: int = 0                              # folder listings made, including those that recognise projects
    stopped: bool = False                                 # a limit was reached

    @property
    def usable(self) -> int:
        return sum(1 for i in self.items if not i.get("errors"))

    def as_dict(self) -> dict:
        return {"selections": self.selections, "found": self.usable, "problems": len(self.items) - self.usable,
                "folders_scanned": self.folders_scanned, "stopped": self.stopped, "warnings": list(self.warnings)}


class _Limit(Exception):
    pass


class _Budget:
    def __init__(self, max_items: int, max_folders: int):
        self.max_items, self.max_folders = max_items, max_folders
        self.items = self.folders = 0

    def item(self) -> None:
        if self.items >= self.max_items:
            raise _Limit(f"scan stopped after {self.max_items} inputs; select a smaller folder")
        self.items += 1

    def folder(self) -> None:
        if self.folders >= self.max_folders:
            raise _Limit(f"scan stopped after {self.max_folders} folder listings; select a smaller folder")
        self.folders += 1


def _key(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def reparse_is_link(st) -> bool:
    """Whether an `lstat` result is a symbolic link, or on Windows a junction or symbolic-link reparse point. Other reparse
    points (OneDrive Files On-Demand placeholders, for example) are ordinary files and folders to us."""
    return stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) in _LINK_TAGS


def _is_link(path) -> bool:
    try:
        return reparse_is_link(os.lstat(path))
    except OSError:
        return True                                             # cannot tell: do not follow


def _inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def _label(path: Path, root: Path) -> str:
    """The path below the selected folder's parent, with forward slashes: `Reports/Finance/Sales.pbix`."""
    try:
        return path.relative_to(root.parent).as_posix()
    except ValueError:
        return path.name


def _problem(path, label: str, message: str) -> dict:
    return {"source": str(path), "label": label, "errors": [{"code": "INVALID_INPUT", "message": message}]}


def discover(paths, *, exclude=(), max_items: int = MAX_ITEMS, max_folders: int = MAX_FOLDERS) -> Discovery:
    """The inputs for `paths` (files and folders), per the rules in this module's docstring."""
    result = Discovery()
    skipped = [_key(p) for p in exclude if p]
    seen: set[str] = set()
    budget = _Budget(max_items, max_folders)
    bucket = result.items                  # selections keep the order they were given; a folder's finds are sorted
    current = Path(".")

    def add(item: dict) -> bool:
        key = _key(item["source"])
        if key in seen:
            return False
        budget.item()
        seen.add(key)
        bucket.append(item)
        return True

    try:
        for raw in paths:
            result.selections += 1
            current = selected = Path(os.path.abspath(raw))
            if not os.path.lexists(selected):
                add(classify(selected))                           # "not found", as before
                continue
            if _is_link(selected):
                add(_problem(selected, selected.name, f"{selected} is a link or junction, and links are not followed; "
                                                      "select the real folder or file"))
                continue
            path = Path(os.path.realpath(selected))
            if any(_inside(_key(path), s) for s in skipped):
                add(_problem(path, path.name, f"{path} is the output folder or the generator's own folder (or inside one), "
                                              "so it is left out"))
                continue
            if not path.is_dir():
                add(classify(path))                               # a file: as before
                continue
            bucket = []
            try:
                matched, problems = _visit(path, result, add, skipped, budget)
                if matched == 0 and problems == 0:
                    add(_problem(path, path.name, f"no supported inputs found in {path} (looked for .pbix, .abf and .bim "
                                                  "files and project folders, in this folder and below)"))
            finally:
                bucket.sort(key=lambda i: i["label"].casefold())
                result.items.extend(bucket)
                bucket = result.items
    except _Limit as limit:
        result.stopped = True
        result.items.append(_problem(current, current.name, str(limit)))
    result.folders_scanned = budget.folders
    return result


def _visit(root: Path, result: Discovery, add, skipped: list[str], budget: _Budget):
    """Search `root` and below. Returns (inputs matched, problems recorded); an input met twice still counts as matched."""
    matched = problems = 0
    stack = [root]
    while stack:
        folder = stack.pop()
        budget.folder()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.casefold())
        except OSError as exc:
            add(_problem(folder, folder.name if folder == root else _label(folder, root),
                         f"cannot read folder {folder}: {exc.strerror or exc}"))
            problems += 1
            continue
        project = classify_folder(folder, children={e.name for e in entries}, on_list=budget.folder)
        if project is not None:                                   # a project (or a broken one) is one input
            project["label"] = folder.name if folder == root else _label(folder, root)
            if project.get("errors"):
                problems += 1
            else:
                matched += 1
            add(project)
            continue
        subfolders = []
        for entry in entries:
            child = folder / entry.name
            try:
                is_dir = entry.is_dir()                           # follows links, so a link to a folder is seen as a folder
            except OSError:
                is_dir = False
            if _is_link(child):                                   # files and folders alike: never followed
                if is_dir or child.suffix.lower() in FILE_SUFFIXES:
                    result.warnings.append(f"Not followed (link or junction): {child}")
                continue
            if is_dir:
                if entry.name.casefold() not in SKIP_FOLDER_NAMES and not any(_inside(_key(child), s) for s in skipped):
                    subfolders.append(child)
                continue
            if entry.name.startswith(("._", "~$")) or child.suffix.lower() not in FILE_SUFFIXES:
                continue
            item = classify(child)
            item["label"] = _label(child, root)
            matched += 1
            add(item)
        stack.extend(reversed(subfolders))                        # depth first, in name order
    return matched, problems
