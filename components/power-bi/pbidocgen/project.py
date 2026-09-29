"""Work out what a person actually handed us.

A Power BI project on disk looks like this:

    Sales.pbip                                  the project file
    Sales.SemanticModel/
        definition/  *.tmdl                     (or model.bim)
    Sales.Report/
        definition.pbir                         points back at the model
        definition/  report.json, pages/…

People point this tool at any level of that tree — the .pbip file, the project
folder, one of the two artifact folders, or a bare model.bim — and reasonably
expect it to work out the rest. `discover()` does that resolution and, when it
finds a report, follows the report's own definition.pbir reference to locate
the matching semantic model, so `--project Sales.Report` documents the pair.

Nothing here parses model or report content; it only resolves paths.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "utf-16")


def _load_json(path: Path):
    for enc in _ENCODINGS:
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        except OSError:
            return None
    return None


def is_semantic_model_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "model.bim").is_file() or (path / "definition" / "model.bim").is_file():
        return True
    if (path / "model.tmdl").is_file() or (path / "definition" / "model.tmdl").is_file():
        return True
    if any(path.glob("*.tmdl")) or any((path / "definition").glob("*.tmdl")):
        return True
    return path.name.lower().endswith(".semanticmodel")


def is_report_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "definition.pbir").is_file():
        return True
    definition = path / "definition"
    if (definition / "report.json").is_file() or (definition / "pages").is_dir():
        return True
    if (path / "report.json").is_file() or (path / "pages").is_dir():
        return True
    return path.name.lower().endswith(".report")


def model_referenced_by(report_dir: Path) -> Path | None:
    """Follow a report's definition.pbir back to its semantic model."""
    pbir = report_dir / "definition.pbir"
    if not pbir.is_file():
        return None
    data = _load_json(pbir) or {}
    ref = (data.get("datasetReference") or {}).get("byPath") or {}
    rel = ref.get("path")
    if not rel:
        return None
    candidate = (report_dir / rel).resolve()
    return candidate if candidate.exists() else None


def _artifacts_from_pbip(pbip: Path) -> tuple[Path | None, Path | None]:
    data = _load_json(pbip) or {}
    root = pbip.parent
    model = report = None
    for artifact in data.get("artifacts", []) or []:
        for key, entry in artifact.items():
            path = (entry or {}).get("path") if isinstance(entry, dict) else None
            if not path:
                continue
            resolved = (root / path).resolve()
            if key.lower() in ("report",) or resolved.name.lower().endswith(".report"):
                report = resolved
            elif key.lower() in ("dataset", "semanticmodel") or \
                    resolved.name.lower().endswith(".semanticmodel"):
                model = resolved
    return model, report


def _scan_project_folder(folder: Path) -> tuple[Path | None, Path | None, list[str]]:
    notes: list[str] = []
    models = sorted(d for d in folder.iterdir()
                    if d.is_dir() and is_semantic_model_dir(d))
    reports = sorted(d for d in folder.iterdir() if d.is_dir() and is_report_dir(d))

    model = models[0] if models else None
    report = reports[0] if reports else None

    # When several artifacts sit side by side, the report's own definition.pbir
    # says which model it belongs to — trust that over alphabetical order.
    referenced = {r: model_referenced_by(r) for r in reports}
    for candidate_report, candidate_model in referenced.items():
        if candidate_model and candidate_model in {m.resolve() for m in models}:
            report = candidate_report
            model = next(m for m in models if m.resolve() == candidate_model)
            break

    if len(models) > 1:
        notes.append(
            f"{len(models)} semantic models found; documenting '{model.name}'. "
            f"Use --model to pick another "
            f"({', '.join(m.name for m in models if m != model)})."
        )
    if len(reports) > 1:
        notes.append(
            f"{len(reports)} reports found; documenting '{report.name}'. "
            f"Use --report to pick another "
            f"({', '.join(r.name for r in reports if r != report)})."
        )
    return model, report, notes


def discover(path: str | Path) -> dict:
    """Resolve any project-ish path into {model, report, title, notes}."""
    path = Path(path)
    notes: list[str] = []
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    model = report = None
    title = None

    if path.is_file() and path.suffix.lower() == ".pbip":
        model, report = _artifacts_from_pbip(path)
        title = path.stem
        # A .pbip normally lists only the report; the semantic model is named by
        # the report's own definition.pbir.
        if report and not model:
            model = model_referenced_by(report)
            if model:
                notes.append(f"Semantic model resolved from definition.pbir: {model.name}")
        if not model and not report:
            model, report, notes = _scan_project_folder(path.parent)

    elif path.is_file():
        # a bare model.bim (or any TMSL json)
        model = path
        title = path.parent.name if path.name.lower() == "model.bim" else path.stem
        title = re.sub(r"\.SemanticModel$", "", title, flags=re.I)

    elif is_report_dir(path) and not is_semantic_model_dir(path):
        report = path
        model = model_referenced_by(path)
        if model:
            notes.append(f"Semantic model resolved from definition.pbir: {model.name}")
        title = re.sub(r"\.Report$", "", path.name, flags=re.I)

    elif is_semantic_model_dir(path):
        model = path
        title = re.sub(r"\.SemanticModel$", "", path.name, flags=re.I)
        sibling = path.parent
        for candidate in sorted(d for d in sibling.iterdir()
                                if d.is_dir() and is_report_dir(d)):
            if model_referenced_by(candidate) == path.resolve():
                report = candidate
                notes.append(f"Matching report found alongside the model: {candidate.name}")
                break

    else:
        model, report, notes = _scan_project_folder(path)
        title = path.name
        pbips = sorted(path.glob("*.pbip"))
        if pbips and not (model or report):
            return discover(pbips[0])
        if pbips:
            title = pbips[0].stem

    if not model and not report:
        raise FileNotFoundError(
            f"'{path}' does not look like a Power BI project. Expected a .pbip "
            f"file, a folder containing *.SemanticModel / *.Report, one of those "
            f"folders, or a model.bim."
        )

    return {"model": model, "report": report, "title": title, "notes": notes}

