"""Assemble an envelope-v1 artifact from an engine's rendered HTML."""
from __future__ import annotations

import hashlib
import html
from datetime import datetime, timezone

from bidoc_contracts import PLACEHOLDER, embed_manifest, validate_artifact

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
    validate_artifact(artifact, view_ids=view_ids)          # never hand out an artifact that fails the contract
    return artifact
