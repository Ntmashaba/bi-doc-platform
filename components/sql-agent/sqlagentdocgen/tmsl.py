"""Analyse TMSL (JSON) command strings from ANALYSISCOMMAND job steps.

Understands the refresh family (type, objects with database/table/partition,
applyRefreshPolicy, effectiveDate), createOrReplace, alter, delete, backup,
restore, and sequence envelopes. Never raises: unparseable JSON yields a
result with ok=False.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TmslOperation:
    kind: str  # refresh | createOrReplace | alter | delete | backup | restore |
               # attach | detach | synchronize | ... (as written)
    verb: Optional[str] = None  # refresh type: full | dataOnly | add | ...
    object_path: Optional[str] = None  # database/table/partition, /-delimited
    reads: List[str] = field(default_factory=list)
    detail: Dict[str, str] = field(default_factory=dict)


@dataclass
class TmslScript:
    ok: bool
    error: Optional[str] = None
    max_parallelism: Optional[int] = None  # sequence envelopes
    operations: List[TmslOperation] = field(default_factory=list)


def _object_path(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    parts = [obj.get(k) for k in ("database", "table", "partition")]
    parts = [p for p in parts if isinstance(p, str) and p]
    return "/".join(parts) if parts else None


def _op_for(kind: str, body: Any) -> List[TmslOperation]:
    if not isinstance(body, dict):
        return [TmslOperation(kind=kind)]
    if kind == "refresh":
        ops: List[TmslOperation] = []
        rtype = body.get("type")
        objects = body.get("objects")
        extra = {
            k: str(v)
            for k, v in body.items()
            if k not in ("type", "objects") and isinstance(v, (str, int, float, bool))
        }
        if isinstance(objects, list) and objects:
            for obj in objects:
                ops.append(
                    TmslOperation(
                        kind="refresh",
                        verb=str(rtype) if rtype else None,
                        object_path=_object_path(obj),
                        detail=dict(extra),
                    )
                )
        else:
            ops.append(
                TmslOperation(
                    kind="refresh",
                    verb=str(rtype) if rtype else None,
                    object_path=_object_path(body),
                    detail=dict(extra),
                )
            )
        return ops
    # createOrReplace / alter / delete carry their target in a nested
    # "object" member; the body itself may hold a definition whose keys must
    # never be mistaken for path components.
    target = body.get("object")
    op = TmslOperation(
        kind=kind,
        object_path=_object_path(target) or _object_path(body),
    )
    for key in ("file", "database", "name"):
        value = body.get(key)
        if isinstance(value, str):
            op.detail[key] = value
    return [op]


def analyse_tmsl(command: str) -> TmslScript:
    """Parse a TMSL JSON command string; never raises."""
    text = command.strip()
    script = TmslScript(ok=False)
    if not text.startswith("{"):
        script.error = "command is not JSON"
        return script
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        script.error = f"malformed JSON: {exc}"
        return script
    if not isinstance(doc, dict):
        script.error = "TMSL root is not an object"
        return script

    script.ok = True
    for key, body in doc.items():
        if key == "sequence" and isinstance(body, dict):
            mp = body.get("maxParallelism")
            if isinstance(mp, int):
                script.max_parallelism = mp
            ops = body.get("operations")
            if isinstance(ops, list):
                for item in ops:
                    if isinstance(item, dict):
                        for sub_key, sub_body in item.items():
                            script.operations.extend(_op_for(sub_key, sub_body))
        else:
            script.operations.extend(_op_for(key, body))
    return script
