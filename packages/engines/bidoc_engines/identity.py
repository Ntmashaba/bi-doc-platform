"""Document and asset identity (handoff sections 5 and 17.2).

A sidecar beside the source (``<name>.bidoc-identity.json``) maps each stream (document type, environment, scope)
to a document ID. The sidecar records the path it was created for: a copied or
moved source must explicitly reuse that identity or start a new one. Nothing is
inferred from file names. If the source folder is not writable, the mapping is
kept in the generator's local mapping folder instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

SIDECAR = ".bidoc-identity.json"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ENV_ALIASES = {"prod": "production", "prd": "production", "dev": "development", "tst": "test"}


class IdentityDecisionRequired(ValueError):
    """The source moved or was copied; the caller must choose 'existing' or 'new'."""


@dataclass(frozen=True)
class Identity:
    asset_id: str
    document_id: str
    stored_in: str


def environment_key(label: str | None) -> str:
    key = re.sub(r"[^a-z0-9_-]+", "-", (label or "").strip().lower()).strip("-_")[:64]
    return ENV_ALIASES.get(key, key) or "unknown"


def _sidecar_path(source: Path) -> Path:
    # Beside the source, never inside it: engines read every JSON file in an input folder.
    return source.with_name(source.name + SIDECAR)


def _mapping_path(source: Path, mapping_dir: Path) -> Path:
    return mapping_dir / (hashlib.sha256(str(source).encode("utf-8")).hexdigest() + ".json")


def _read(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = (isinstance(data, dict) and data.get("version") == 1 and _UUID.match(str(data.get("asset_id", "")))
          and isinstance(data.get("streams"), dict))
    return data if ok else None


def _write(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def resolve(source, stream_key: str, *, mapping_dir, choice: str | None = None,
            document_id: str | None = None) -> Identity:
    """Return (and persist) the identity for one stream of a source.

    choice: None (default), "existing" (reuse a moved/copied source's identity) or "new".
    document_id: explicitly reuse a known document ID for this stream.
    """
    source = Path(source).resolve()
    if document_id is not None and not _UUID.match(document_id):
        raise ValueError("document_id must be a lower-case UUID")
    side, mapped = _sidecar_path(source), _mapping_path(source, Path(mapping_dir))
    data = _read(side) or _read(mapped)
    if data and data.get("path") != str(source):
        if choice is None:
            raise IdentityDecisionRequired(
                f"{source} carries the identity of {data.get('path')}; choose 'existing' to keep documenting "
                "the same report or 'new' to start a separate one")
        if choice == "new":
            data = None
    if choice == "new" and data and data.get("path") == str(source):
        data = None
    if not data:
        data = {"version": 1, "asset_id": str(uuid.uuid4()), "streams": {}}
    data["path"] = str(source)
    if document_id:
        data["streams"][stream_key] = document_id
    data["streams"].setdefault(stream_key, str(uuid.uuid4()))
    where = side if _write(side, data) else mapped if _write(mapped, data) else None
    if where is None:
        raise OSError("could not persist document identity next to the source or in the local mapping folder")
    return Identity(data["asset_id"], data["streams"][stream_key], str(where))
