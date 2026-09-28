"""Locate, embed and hash the inert `pbidoc-manifest` element (handoff section 5).

Everything works on bytes. Nothing here parses or executes HTML beyond locating the
one manifest element, so producer and consumer compute identical hashes.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

MANIFEST_ID = "pbidoc-manifest"
PLACEHOLDER = b"<!--PBIDOC-MANIFEST-->"

_SCRIPT_OPEN = re.compile(rb"<script\b([^>]*)>", re.IGNORECASE)
_SCRIPT_CLOSE = re.compile(rb"</script\s*>", re.IGNORECASE)
_ATTR = re.compile(rb"""([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")


class ManifestLocationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManifestLocation:
    start: int          # offset of "<script"
    end: int            # offset just after the closing "</script>"
    body: bytes         # the JSON text between the tags
    attributes: dict


def _attributes(raw: bytes) -> dict:
    attrs = {}
    for m in _ATTR.finditer(raw):
        name = m.group(1).decode("ascii", "replace").lower()
        value = next((g for g in m.group(2, 3, 4) if g is not None), b"")
        attrs.setdefault(name, value.decode("utf-8", "replace"))
    return attrs


def locate_manifest(html: bytes) -> ManifestLocation:
    """Return the single manifest element; reject zero or several."""
    found = []
    for m in _SCRIPT_OPEN.finditer(html):
        attrs = _attributes(m.group(1))
        if attrs.get("id") != MANIFEST_ID:
            continue
        close = _SCRIPT_CLOSE.search(html, m.end())
        if close is None:
            raise ManifestLocationError("MANIFEST_UNTERMINATED", "The manifest element has no closing tag.")
        found.append(ManifestLocation(m.start(), close.end(), html[m.end():close.start()], attrs))
    if not found:
        raise ManifestLocationError("MANIFEST_MISSING", "No pbidoc-manifest element was found.")
    if len(found) > 1:
        raise ManifestLocationError("MANIFEST_DUPLICATE", f"{len(found)} pbidoc-manifest elements were found.")
    return found[0]


def content_sha256(html: bytes) -> str:
    """SHA-256 of the artifact with the manifest element replaced by PLACEHOLDER."""
    loc = locate_manifest(html)
    return hashlib.sha256(html[:loc.start] + PLACEHOLDER + html[loc.end:]).hexdigest()


def serialize_manifest(manifest: dict) -> str:
    """Canonical JSON for the element body; '<' is escaped so no text can close the script."""
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text.replace("<", "\\u003c")


def embed_manifest(html_with_placeholder: bytes, manifest: dict) -> bytes:
    """Producer side: set content_sha256 and replace the single PLACEHOLDER with the manifest element.

    The input must contain PLACEHOLDER exactly once and no manifest element, so the hash
    computed here equals what content_sha256() computes on the result.
    """
    if html_with_placeholder.count(PLACEHOLDER) != 1:
        raise ValueError("The document must contain the manifest placeholder exactly once.")
    if any(_attributes(m.group(1)).get("id") == MANIFEST_ID for m in _SCRIPT_OPEN.finditer(html_with_placeholder)):
        raise ValueError("The document already contains a manifest element.")
    manifest = dict(manifest, content_sha256=hashlib.sha256(html_with_placeholder).hexdigest())
    element = (f'<script type="application/json" id="{MANIFEST_ID}">'
               f"{serialize_manifest(manifest)}</script>").encode("utf-8")
    return html_with_placeholder.replace(PLACEHOLDER, element)
