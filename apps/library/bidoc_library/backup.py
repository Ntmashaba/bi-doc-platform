"""Backup, verify and restore (handoff 12, 17.3; B13, A14, A28).

A backup is a folder:

    backup.json        format, backend, library version, date, catalogue sequence, counts,
                       and every file with its SHA-256 and size
    LocalStore:  catalogue.sqlite3 (SQLite online backup) and files/<path> for every
                 artifact, derived snapshot, relationship generation and release
    AzureStore:  table.jsonl (every entity of the library table) and blobs/<key>

It holds the committed catalogue at one sequence: documents, revisions, metadata overrides
and audit, manual assertions and audit, generation pointers, and every immutable blob they
reference (retention never prunes a referenced revision or generation). Restore goes into
an empty deployment of the same backend, checks every file against backup.json first, then
opens the restored library and reads every committed revision back through its checksum.
Derived search and relationship snapshots are rebuilt by the library if anything is stale.

Session secrets, staging uploads and partial files are not backed up. Publishing tokens
are (hashes only), so revocations survive a restore.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .errors import LibraryError

FORMAT = "bidoc-backup/1"
INDEX = "backup.json"
SKIP_LOCAL = {"catalogue.sqlite3", "catalogue.sqlite3-wal", "catalogue.sqlite3-shm", "catalogue.sqlite3-journal",
              "session-secret", "staging"}
SEQUENCE_RETRIES = 5


class BackupError(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prepare(dest: Path) -> Path:
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise BackupError(f"{dest} is not empty; back up into a new folder")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _write(dest: Path, rel: str, data: bytes, files: list) -> None:
    out = dest / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    files.append({"path": rel, "sha256": _sha(data), "bytes": len(data)})


def _safe_rel(rel: str) -> str:
    parts = rel.split("/")
    if not rel or rel.startswith("/") or "\\" in rel or any(p in ("", ".", "..") for p in parts) or ":" in parts[0]:
        raise BackupError(f"unsafe path in backup: {rel!r}")
    return rel


# ---- backup ------------------------------------------------------------------------------

def backup(store, destination) -> dict:
    dest = _prepare(destination)
    files: list = []
    if getattr(store, "backend", "local") == "azure":
        extra = _backup_azure(store, dest, files)
    else:
        extra = _backup_local(store, dest, files)
    index = {"format": FORMAT, "backend": getattr(store, "backend", "local"), "library_version": __version__, "created_at": _now(),
             **extra, "files": files}
    (dest / INDEX).write_text(json.dumps(index, indent=1), encoding="utf-8")
    return {k: v for k, v in index.items() if k != "files"} | {"file_count": len(files)}


def _backup_local(store, dest: Path, files: list) -> dict:
    root = Path(store.root)
    with store._db() as conn:
        target = sqlite3.connect(dest / "catalogue.sqlite3")
        try:
            conn.backup(target)                              # one consistent catalogue
        finally:
            target.close()
    copy = sqlite3.connect(dest / "catalogue.sqlite3")      # read the snapshot, not the live catalogue
    try:
        seq = copy.execute("SELECT sequence FROM catalogue_state WHERE id=1").fetchone()[0]
        committed = copy.execute("SELECT artifact_key, stored_artifact_sha256 FROM revisions "
                                 "WHERE status='committed'").fetchall()
    finally:
        copy.close()
    data = (dest / "catalogue.sqlite3").read_bytes()
    files.append({"path": "catalogue.sqlite3", "sha256": _sha(data), "bytes": len(data)})
    # Blobs are immutable and written before their catalogue commit, so every file the
    # snapshot references already exists; copying after the snapshot can only add extras.
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel.split("/")[0] in SKIP_LOCAL or path.suffix in (".tmp", ".partial"):
            continue
        _write(dest, "files/" + rel, path.read_bytes(), files)
    by_path = {f["path"]: f["sha256"] for f in files}
    for key, digest in committed:
        if by_path.get("files/" + key) != digest:
            raise BackupError(f"committed artifact {key} is missing or failed its integrity check")
    return {"catalogue_sequence": seq,
            "counts": {"committed_revisions": len(committed)}}


def _entity_json(e: dict) -> dict:
    out = {}
    for k, v in e.items():
        if k in ("etag", "Timestamp"):
            continue
        v = getattr(v, "value", v)                           # azure EntityProperty (Int64 etc.)
        if not isinstance(v, (str, int, float, bool)):
            raise BackupError(f"unsupported value type {type(v).__name__} in {e.get('PartitionKey')}/{e.get('RowKey')}")
        out[k] = v
    return out


def _backup_azure(store, dest: Path, files: list) -> dict:
    for _ in range(SEQUENCE_RETRIES):
        before = store.catalogue_sequence()
        entities = [_entity_json(e) for e in store.tables.scan()]
        keys = store.blobs.list("")                          # listed after the scan: a superset
        if store.catalogue_sequence() == before:
            break
        time.sleep(0.5)
    else:
        raise BackupError("the catalogue kept changing during the backup; try again when it is quieter")
    lines = "".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in entities)
    _write(dest, "table.jsonl", lines.encode("utf-8"), files)
    for key in keys:
        _write(dest, "blobs/" + _safe_rel(key), store.blobs.read(key), files)
    committed = 0
    blob_sha = {f["path"][len("blobs/"):]: f["sha256"] for f in files if f["path"].startswith("blobs/")}
    revs = {(e["PartitionKey"], e["RowKey"]): e for e in entities if e["RowKey"].startswith("rev:")}
    for e in entities:
        if e["PartitionKey"] == "documents" and e["RowKey"].startswith("evrev:"):
            rev_id = e["RowKey"][len("evrev:"):]
            rev = next((r for (p, k), r in revs.items() if k == "rev:" + rev_id), None)
            if rev is None or blob_sha.get(rev["artifact_key"]) != rev["stored_artifact_sha256"]:
                raise BackupError(f"committed revision {rev_id} is missing its artifact or failed its integrity check")
            committed += 1
    return {"catalogue_sequence": before, "counts": {"committed_revisions": committed, "entities": len(entities),
                                                      "blobs": len(keys)}}


# ---- verify ------------------------------------------------------------------------------

def verify(source) -> dict:
    """Check backup.json and every file's SHA-256 without touching any library."""
    src = Path(source)
    try:
        index = json.loads((src / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"{src} has no readable {INDEX}: {exc}") from None
    if index.get("format") != FORMAT:
        raise BackupError(f"unsupported backup format {index.get('format')!r}")
    listed = set()
    for f in index["files"]:
        rel = _safe_rel(f["path"])
        listed.add(rel)
        try:
            data = (src / rel).read_bytes()
        except OSError:
            raise BackupError(f"{rel} is missing from the backup") from None
        if _sha(data) != f["sha256"] or len(data) != f["bytes"]:
            raise BackupError(f"{rel} does not match its checksum; the backup is damaged")
    extra = {p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file()} - listed - {INDEX}
    if extra:
        raise BackupError(f"unexpected files in the backup: {', '.join(sorted(extra)[:5])}")
    return index


# ---- restore -----------------------------------------------------------------------------

def restore(source, *, settings, limits, store=None) -> dict:
    """Restore into an empty deployment of the backup's backend, then read everything back.

    `store` (tests) is an already-open, empty Azure store; otherwise the configured one opens.
    """
    from .api import open_store  # noqa: PLC0415
    index = verify(source)
    src = Path(source)
    backend = getattr(store, "backend", "local") if store is not None else settings.data_backend
    if index["backend"] != backend:
        raise BackupError(f"this is a {index['backend']} backup; the configured backend is {backend}")
    if index["backend"] == "local":
        root = Path(settings.local_data_dir)
        if root.exists() and any(p.name != "session-secret" for p in root.iterdir()):
            raise BackupError(f"{root} is not empty; restore into an empty data folder")
        root.mkdir(parents=True, exist_ok=True)
        for f in index["files"]:
            rel = f["path"]
            target = root / (rel[len("files/"):] if rel.startswith("files/") else rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / rel, target)
        store = open_store(settings, limits)
    else:
        store = store or open_store(settings, limits)
        fresh = [(e["PartitionKey"], e["RowKey"], e.get("sequence")) for e in store.tables.scan()]
        if fresh not in ([], [("documents", "state", 0)]) or store.blobs.list(""):
            raise BackupError("the Azure table or container is not empty; restore into a new, empty one")
        for f in index["files"]:
            if f["path"].startswith("blobs/"):
                store.blobs.create(f["path"][len("blobs/"):], (src / f["path"]).read_bytes())
        by_partition: dict[str, list] = {}
        for line in (src / "table.jsonl").read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            by_partition.setdefault(e.pop("PartitionKey"), []).append(e)
        for partition, rows in by_partition.items():
            for i in range(0, len(rows), 100):
                store.tables.transact(partition, [("upsert", r) for r in rows[i:i + 100]])
    return check(store, expected_sequence=index["catalogue_sequence"])


def check(store, *, expected_sequence=None) -> dict:
    """Read every committed revision of every document back through its checksum."""
    seq = store.catalogue_sequence()
    if expected_sequence is not None and seq != expected_sequence:
        raise BackupError(f"restored catalogue is at sequence {seq}, the backup recorded {expected_sequence}")
    documents = revisions = 0
    for doc in store.catalogue_documents():
        documents += 1
        cursor = None
        while True:
            page = store.list_revisions(doc["document_id"], cursor=cursor, limit=200)
            for rev in page["items"]:
                try:
                    store.read_artifact(doc["document_id"], rev["revision_id"])
                except LibraryError as exc:
                    raise BackupError(f"{doc['document_id']}/{rev['revision_id']}: {exc}") from None
                revisions += 1
            cursor = page.get("next_cursor")
            if not cursor:
                break
    return {"catalogue_sequence": seq, "documents": documents, "revisions": revisions}
