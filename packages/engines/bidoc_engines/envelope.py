"""Assemble an envelope-v1 artifact from an engine's rendered HTML."""
from __future__ import annotations

import hashlib
import html
from dataclasses import replace
from datetime import datetime, timezone

from bidoc_contracts import PLACEHOLDER, ContractError, Limits, embed_manifest, locate_manifest, validate_artifact

SECTION_TEXT_LIMIT = 100_000


def anchor(prefix: str, object_id: str) -> str:
    """Stable, valid section anchor for an object id."""
    return f"pbidoc-{prefix}-{hashlib.sha256(object_id.encode('utf-8')).hexdigest()[:16]}"


def section(sid: str, title: str, lines) -> dict:
    text = "\n".join(str(x) for x in lines if x not in (None, ""))
    return {"id": sid, "title": (title or sid).strip()[:200] or sid, "text": text[:SECTION_TEXT_LIMIT]}


def fit_sections(sections, limit_bytes: int = 4 * 1024 * 1024):
    """Trim the longest section texts until the combined size fits; return a warning if trimmed."""
    sizes = [len(s["text"].encode("utf-8")) for s in sections]
    if sum(sizes) <= limit_bytes:
        return sections, None
    cap = max(sizes)
    while sum(min(x, cap) for x in sizes) > limit_bytes and cap > 0:
        cap = int(cap * 0.9)
    trimmed = 0
    for s, size in zip(sections, sizes):
        if size > cap:
            s["text"] = s["text"].encode("utf-8")[:cap].decode("utf-8", "ignore")
            trimmed += 1
    return sections, f"Search text trimmed in {trimmed} section(s) to fit the 4 MiB search limit."


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Generation ceiling: a deliberate, much larger bound than the publication default so that a large but
# valid document is still written locally. It guards memory and disk, not the library's policy.
GENERATION_LIMITS = replace(Limits(), html_bytes=256 * 1024 * 1024, manifest_bytes=128 * 1024 * 1024)


def _section_block(sections) -> str:
    """A static, script-free copy of the searchable sections; also the anchor targets."""
    parts = ['<div id="pbidoc-sections" hidden>']
    for s in sections:
        parts.append(f'<section id="{s["id"]}"><h2>{html.escape(s["title"])}</h2>'
                     f'<pre>{html.escape(s["text"])}</pre></section>')
    parts.append("</div>")
    return "\n".join(parts)


def assemble(engine_html: str, manifest: dict, view_ids) -> bytes:
    """Insert the placeholder and section block, embed the manifest, validate the result."""
    head, body = engine_html.find("</head>"), engine_html.rfind("</body>")
    if head < 0 or body < 0 or body < head:
        raise ValueError("engine output has no </head> or </body>")
    if PLACEHOLDER.decode() in engine_html:
        raise ValueError("engine output already contains the manifest placeholder")
    page = (engine_html[:head] + PLACEHOLDER.decode() + "\n" + engine_html[head:body]
            + _section_block(manifest["sections"]) + "\n" + engine_html[body:])
    artifact = embed_manifest(page.encode("utf-8"), manifest)
    validate_artifact(artifact, limits=GENERATION_LIMITS, view_ids=view_ids)   # structure must hold; size has a hard ceiling
    return artifact


_CATEGORIES = {"ARTIFACT_TOO_LARGE": ("document", "html_bytes", "the library's HTML limit (MAX_HTML_BYTES)"),
               "MANIFEST_TOO_LARGE": ("manifest", "manifest_bytes", "the library's manifest limit (MAX_MANIFEST_BYTES)")}


def publication_problem(artifact: bytes, view_ids=None, limits: Limits | None = None) -> dict | None:
    """None when the library would accept the document at these limits, else a structured reason.

    Publication limits are a library policy, not a generation limit: a document above them is still a
    valid local document. The reason names the category that was exceeded, its measured size and its limit, so
    that the remedy matches (raising the HTML limit does not fix an oversized manifest)."""
    limits = limits or Limits()
    try:
        validate_artifact(artifact, limits=limits, view_ids=view_ids)
    except ContractError as exc:
        category, field_name, setting = _CATEGORIES.get(exc.code, ("contract", None, None))
        size = limit = None
        if field_name:
            limit = getattr(limits, field_name)
            size = len(artifact)
            if category == "manifest":
                size = len(locate_manifest(artifact).body)
        return {"code": exc.code, "category": category, "size_bytes": size, "limit_bytes": limit,
                "setting": setting, "message": str(exc)}
    return None


def publication_warning(problem: dict, file_name: str) -> str:
    mib = lambda n: f"{n / 1048576:.1f} MiB"
    if problem["limit_bytes"] is None:
        return (f"{problem['code']}: the document cannot be published ({problem['message']}) but was written and is "
                f"usable locally: {file_name}.")
    return (f"{problem['code']}: the {problem['category']} is {mib(problem['size_bytes'])} and {problem['setting']} is "
            f"{mib(problem['limit_bytes'])}, so the document cannot be published. It was written and is usable "
            f"locally: {file_name}. To publish it, ask the library administrator to raise that limit; the generator "
            "does not shrink or drop content.")


def size_breakdown(artifact: bytes, manifest: dict) -> dict:
    """Byte counts per category; carries no names, paths or content."""
    import json  # noqa: PLC0415
    enc = lambda v: len(json.dumps(v, ensure_ascii=False).encode("utf-8"))
    payload = manifest.get("native_payload")
    return {"document": len(artifact), "manifest": enc(manifest),
            "model_payload_in_manifest": enc(payload) if payload is not None else 0,
            "section_text": sum(len(x["text"].encode("utf-8")) for x in manifest["sections"])}
