"""Local generation history (handoff sections 4 and 9).

SQLite in the generator's home folder. Desktop processing is not a background service:
on the next launch, anything that was still active is marked `interrupted` and can be
retried. Nothing claims it carried on after the app exited.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ACTIVE = ("queued", "validating", "extracting", "analysing", "rendering")
TERMINAL = ("completed", "local_only", "failed", "cancelled", "interrupted")
RETRYABLE = ("failed", "cancelled", "interrupted")

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, output_dir TEXT NOT NULL, options TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    item_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, position INTEGER NOT NULL,
    engine TEXT, kind TEXT, source TEXT NOT NULL, label TEXT NOT NULL,
    state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, elapsed_seconds REAL,
    artifact_path TEXT, document_id TEXT, revision_id TEXT,
    warnings TEXT NOT NULL DEFAULT '[]', errors TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS items_by_batch ON items (batch_id, position);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class History:
    def __init__(self, home, *, workspace_retention_seconds: float = 7 * 86400):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.workspaces = self.home / "workspaces"
        self.workspaces.mkdir(exist_ok=True)
        self.db_path = self.home / "history.sqlite3"
        self.retention = workspace_retention_seconds
        with self._db() as conn:
            conn.executescript(SCHEMA)
        self.interrupted = self._mark_interrupted()
        self.cleanup_workspaces()

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _mark_interrupted(self) -> int:
        err = json.dumps([{"code": "INTERRUPTED", "message": "The generator closed while this item was running. "
                                                            "Retry it."}])
        with self._db() as conn:
            marks = ",".join("?" * len(ACTIVE))
            cur = conn.execute(f"UPDATE items SET state='interrupted', finished_at=?, errors=? "
                               f"WHERE state IN ({marks})", (now(), err, *ACTIVE))
            return cur.rowcount

    def cleanup_workspaces(self) -> list[str]:
        """Remove workspaces of finished items, and anything older than the retention period."""
        removed = []
        with self._db() as conn:
            states = dict(conn.execute("SELECT item_id, state FROM items").fetchall())
        cutoff = time.time() - self.retention
        for path in self.workspaces.iterdir():
            state = states.get(path.name)
            if state in ACTIVE:
                continue
            # Failed items keep their workspace (pbi-tools log) for the retention period.
            if state in ("completed", "local_only") or state is None or path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path.name)
        return removed

    def workspace(self, item_id: str) -> Path:
        path = self.workspaces / item_id
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True)
        return path

    def release_workspace(self, item_id: str) -> None:
        shutil.rmtree(self.workspaces / item_id, ignore_errors=True)

    # ---- records ----------------------------------------------------------------

    def create_batch(self, output_dir, options: dict, items: list[dict]) -> str:
        batch_id = str(uuid.uuid4())
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO batches VALUES (?,?,?,?)", (batch_id, now(), str(output_dir), json.dumps(options)))
            for i, it in enumerate(items):
                state = "queued" if not it.get("errors") else "failed"
                conn.execute("INSERT INTO items (item_id, batch_id, position, engine, kind, source, label, state, "
                             "created_at, finished_at, errors) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (str(uuid.uuid4()), batch_id, i, it.get("engine"), it.get("kind"), it["source"],
                              it["label"], state, now(), None if state == "queued" else now(),
                              json.dumps(it.get("errors") or [])))
            conn.execute("COMMIT")
        return batch_id

    def update(self, item_id, **fields) -> None:
        for key in ("warnings", "errors"):
            if key in fields:
                fields[key] = json.dumps(fields[key])
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._db() as conn:
            conn.execute(f"UPDATE items SET {cols} WHERE item_id=?", (*fields.values(), item_id))

    def claim(self, item_id, attempt) -> bool:
        """queued → validating, atomically; False if it was cancelled meanwhile."""
        with self._db() as conn:
            return conn.execute("UPDATE items SET state='validating', started_at=?, attempt=? "
                                "WHERE item_id=? AND state='queued'", (now(), attempt, item_id)).rowcount == 1

    def cancel_queued(self, item_id) -> bool:
        with self._db() as conn:
            return conn.execute("UPDATE items SET state='cancelled', finished_at=? WHERE item_id=? AND state='queued'",
                                (now(), item_id)).rowcount == 1

    def requeue(self, item_id) -> dict:
        with self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM items WHERE item_id=?", (item_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(item_id)
            if row["state"] not in RETRYABLE:
                conn.execute("ROLLBACK")
                raise ValueError(f"only failed, cancelled or interrupted items can be retried (this one is {row['state']})")
            conn.execute("UPDATE items SET state='queued', started_at=NULL, finished_at=NULL, elapsed_seconds=NULL, "
                         "errors='[]', warnings='[]' WHERE item_id=?", (item_id,))
            conn.execute("COMMIT")
        return self.item(item_id)

    @staticmethod
    def _item(row) -> dict:
        d = dict(row)
        d["warnings"], d["errors"] = json.loads(d["warnings"]), json.loads(d["errors"])
        return d

    def item(self, item_id) -> dict:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._item(row)

    def batch(self, batch_id) -> dict:
        with self._db() as conn:
            b = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if b is None:
                raise KeyError(batch_id)
            rows = conn.execute("SELECT * FROM items WHERE batch_id=? ORDER BY position", (batch_id,)).fetchall()
        return {"batch_id": b["batch_id"], "created_at": b["created_at"], "output_dir": b["output_dir"],
                "options": json.loads(b["options"]), "items": [self._item(r) for r in rows]}

    def batches(self, limit=50) -> list[dict]:
        with self._db() as conn:
            ids = [r[0] for r in conn.execute("SELECT batch_id FROM batches ORDER BY created_at DESC LIMIT ?",
                                              (limit,))]
        return [self.batch(i) for i in ids]

    def queued(self) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute("SELECT i.* FROM items i JOIN batches b ON b.batch_id=i.batch_id "
                                "WHERE i.state='queued' ORDER BY b.created_at, i.position").fetchall()
        return [self._item(r) for r in rows]
