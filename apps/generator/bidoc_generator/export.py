"""Portable offline library export (handoff 9, 17.6; B12, A12, A38).

`bidoc export-library OUT INPUT...` writes a folder that opens from file:// with the
network off:

    index.html          catalogue, search and viewer; data and code inline (no fetch,
                        no modules, since file:// cannot load them)
    documents/<id>.html each document re-rendered by the trusted engines, read-only
    export.json         what was exported: versions, date, documents, rules

The snapshot pins document revisions and one relationship generation computed over every
input. Only the documents selected with --only (default: all) are copied; links to the
others are shown as "Not included". It is a static snapshot: nothing watches folders or
changes later, and re-running the export rebuilds it.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from . import __version__

FORMAT = "bidoc-portable/1"
MARKER = "export.json"


class ExportError(Exception):
    pass


def _bundle() -> str:
    """One classic script from the compiled ES modules (search, protocol, offline)."""
    static = resources.files("bidoc_generator").joinpath("offline_static")
    parts = []
    for name in ("search.js", "protocol.js", "offline.js"):
        text = static.joinpath(name).read_text(encoding="utf-8")
        text = re.sub(r"^import [^\n]*\n", "", text, flags=re.M)
        text = re.sub(r"^export (?=(function|class|const|let|async function)\b)", "", text, flags=re.M)
        if re.search(r"^\s*(import|export)\b", text, flags=re.M):
            raise ExportError(f"unexpected module syntax left in {name}")
        parts.append(f"// ---- {name}\n{text}")
    return '"use strict";\n(() => {\n' + "\n".join(parts) + "\n})();\n"


def _collect(inputs, warnings):
    from bidoc_contracts import ContractError, validate_artifact  # noqa: PLC0415
    from bidoc_engines.envelope import GENERATION_LIMITS  # noqa: PLC0415
    found = {}
    for raw in inputs:
        path = Path(raw)
        files = sorted(path.rglob("*.html")) if path.is_dir() else [path]
        for f in files:
            if f.name == "index.html" and (f.parent / MARKER).exists():
                continue                                     # a previous export's own page
            try:
                manifest = validate_artifact(f.read_bytes(), limits=GENERATION_LIMITS)   # a local hub is not publication
            except OSError as exc:
                raise ExportError(f"cannot read {f}: {exc}") from None
            except ContractError as exc:
                warnings.append(f"skipped {f.name}: {'no publication manifest (local-only output)' if exc.code == 'MANIFEST_MISSING' else exc}")
                continue
            prior = found.get(manifest["document_id"])
            if prior is None or manifest["generated_at"] > prior["generated_at"]:
                found[manifest["document_id"]] = manifest
    return found


def export_library(inputs, out_dir, *, only=None, title="BI documentation") -> dict:
    from bidoc_contracts import validate_artifact  # noqa: PLC0415
    from bidoc_engines.convert import UnsupportedProjection, rerender  # noqa: PLC0415
    from bidoc_engines.envelope import GENERATION_LIMITS  # noqa: PLC0415
    from bidoc_relationships import RULE_VERSION, Document, detect  # noqa: PLC0415

    out = Path(out_dir)
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
        raise ExportError(f"{out} is not empty and is not a previous export; choose an empty folder")
    warnings = []
    manifests = _collect(inputs, warnings)
    if not manifests:
        raise ExportError("no documents with a publication manifest were found in the inputs")
    selected = set(only) if only else set(manifests)
    unknown = selected - set(manifests)
    if unknown:
        raise ExportError(f"--only names documents that are not in the inputs: {', '.join(sorted(unknown))}")

    rendered = {}
    for doc_id, m in manifests.items():
        try:
            html, stored = rerender(m)
        except UnsupportedProjection as exc:
            raise ExportError(f"{m['title']}: {exc}") from None
        validate_artifact(html, limits=GENERATION_LIMITS)
        rendered[doc_id] = (html, stored)

    docs = [Document(d, s["revision_id"], s["document_type"], s["publication"]["environment_key"],
                     tuple(s["objects"])) for d, (_, s) in sorted(rendered.items())]
    links = [{k: r[k] for k in ("relationship_id", "kind", "confidence", "source_document_id", "source_object_id",
                                 "target_document_id", "target_object_id", "source_parent_object_id")}
             for r in detect(docs)
             if r["source_document_id"] in selected or r["target_document_id"] in selected]

    if (out / "documents").exists():
        shutil.rmtree(out / "documents")                     # rebuild, never merge with an old snapshot
    (out / "documents").mkdir(parents=True, exist_ok=True)
    documents = []
    for doc_id in sorted(selected):
        html, s = rendered[doc_id]
        rel = f"documents/{doc_id}.html"
        (out / rel).write_bytes(html)
        targets = {t["target_id"]: {"view_id": t["view_id"], "args": t["args"]} for t in s["navigation"]["targets"]}
        sections = {x["id"] for x in s["sections"]}
        documents.append({
            "document_id": doc_id, "revision_id": s["revision_id"], "document_type": s["document_type"],
            "title": s["title"], "tags": s["tags"], "classification": s["classification"], "file": rel,
            "sections": [{"id": x["id"], "title": x["title"], "text": x["text"]} for x in s["sections"]],
            "objects": [{"object_id": o["object_id"], "label": o["label"], "kind": o["kind"],
                         "section_id": o["section_id"], "parent_object_id": o["parent_object_id"],
                         "view": targets.get(o["object_id"])} for o in s["objects"]],
            "section_targets": [{"section_id": t, "view": v} for t, v in targets.items() if t in sections]})
    snapshot = {
        "format": FORMAT, "title": title[:200], "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator_version": __version__, "rule_version": RULE_VERSION,
        "projection_policies": sorted({s["projection"]["policy_version"] for _, s in rendered.values()}),
        "documents": documents,
        "not_included": [{"document_id": d, "title": rendered[d][1]["title"],
                          "document_type": rendered[d][1]["document_type"]} for d in sorted(set(rendered) - selected)],
        "relationships": links}
    (out / "index.html").write_text(_page(snapshot), encoding="utf-8")
    meta = {k: snapshot[k] for k in ("format", "title", "created_at", "generator_version", "rule_version",
                                     "projection_policies")}
    meta["documents"] = [{"document_id": d["document_id"], "revision_id": d["revision_id"], "title": d["title"],
                          "file": d["file"]} for d in documents]
    meta["relationships"] = len(links)
    meta["warnings"] = warnings
    (out / MARKER).write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; "
       "frame-src 'self' file:; connect-src 'none'; form-action 'none'; base-uri 'none'")

STYLE = """
:root { color-scheme: light dark; --fg:#1d2330; --muted:#5b6475; --bg:#f6f7f9; --card:#fff; --line:#d9dde5; --accent:#2657c5;
  font: 15px/1.45 system-ui, "Segoe UI", sans-serif; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6e9ef; --muted:#a3abba; --bg:#14171d; --card:#1c2029; --line:#313746; --accent:#7aa2ff; } }
body { margin:0; color:var(--fg); background:var(--bg); }
main { padding:16px 20px; max-width:1400px; margin:0 auto; }
a { color:var(--accent); } .muted { color:var(--muted); } .small { font-size:.88rem; }
input[type=search] { width:100%; max-width:640px; padding:8px 10px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); font:inherit; }
nav.types a { margin-right:14px; } nav.types a[aria-current=page] { font-weight:700; }
ol.hits { padding-left:1.2rem; } ol.hits li { margin:.5rem 0; } .snippet { margin:.2rem 0 0; color:var(--muted); }
.badge { font-size:.75rem; border:1px solid var(--line); border-radius:4px; padding:0 5px; margin-right:6px; }
.toolbar { display:flex; gap:12px; align-items:center; margin-bottom:8px; }
.split { display:grid; grid-template-columns:1fr 320px; gap:14px; }
iframe.viewer { width:100%; height:78vh; border:1px solid var(--line); border-radius:8px; background:#fff; }
aside.related { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 14px; overflow:auto; max-height:78vh; }
aside.related h2 { font-size:1rem; margin:.2rem 0 .6rem; } aside.related h3 { font-size:.9rem; margin:.6rem 0 .2rem; }
.not-included { color:var(--muted); }
@media (max-width: 900px) { .split { grid-template-columns:1fr; } }
"""


def _page(snapshot: dict) -> str:
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    title = snapshot["title"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (f'<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
            f'<meta name="referrer" content="no-referrer">\n<title>{title}</title>\n<style>{STYLE}</style>\n</head>\n'
            f'<body>\n<main id="main"><p>Loading…</p></main>\n'
            f'<script type="application/json" id="snapshot">{data}</script>\n'
            f'<script>\n{_bundle()}</script>\n</body>\n</html>\n')
