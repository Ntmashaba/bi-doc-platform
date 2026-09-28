"""Versioned, repeatable catalogue migrations for the local SQLite store.

Each migration runs once, inside a transaction, and is recorded in schema_migrations.
Back up before upgrading: `LocalStore.backup(path)` uses SQLite's online backup API.
"""
from __future__ import annotations

import sqlite3

MIGRATIONS = [
    (1, "catalogue, revisions, imports, events, relationships", """
    CREATE TABLE catalogue_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        sequence INTEGER NOT NULL                 -- advances with every committed change; derived state rebuilds below it
    );
    INSERT INTO catalogue_state (id, sequence) VALUES (1, 0);

    CREATE TABLE documents (
        document_id TEXT PRIMARY KEY,
        document_type TEXT NOT NULL CHECK (document_type IN ('power_bi', 'adf')),
        asset_id TEXT NOT NULL,                   -- immutable stream tuple (17.2)
        environment_key TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        business_area TEXT NOT NULL, environment TEXT NOT NULL, owner TEXT NOT NULL,
        title TEXT NOT NULL, description TEXT NOT NULL, tags TEXT NOT NULL,   -- tags: JSON array
        current_revision_id TEXT NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        etag TEXT NOT NULL,
        UNIQUE (asset_id, document_type, environment_key, scope_key)
    );

    CREATE TABLE revisions (                      -- immutable descriptors; prepared before commit
        document_id TEXT NOT NULL,
        revision_id TEXT NOT NULL PRIMARY KEY,
        document_type TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        generated_at TEXT NOT NULL,
        published_at TEXT,                        -- set at commit
        publisher_subject TEXT,
        artifact_key TEXT NOT NULL,
        submitted_artifact_sha256 TEXT NOT NULL,
        stored_artifact_sha256 TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        section_count INTEGER NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('prepared', 'committed', 'conflicted'))
    );
    CREATE INDEX revisions_by_document ON revisions (document_id, status);
    CREATE INDEX revisions_by_submitted ON revisions (submitted_artifact_sha256);

    CREATE TABLE imports (
        import_id TEXT PRIMARY KEY,
        subject TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,             -- submitted bytes + target + precondition
        submitted_artifact_sha256 TEXT NOT NULL,
        document_id TEXT, revision_id TEXT,
        target_document_id TEXT,
        expected_etag TEXT,
        state TEXT NOT NULL CHECK (state IN ('validating', 'prepared', 'committed', 'conflicted', 'failed')),
        error_code TEXT, error_message TEXT,
        committed_event_id INTEGER, catalogue_sequence INTEGER,
        duplicate_of TEXT,
        created_at TEXT NOT NULL, completed_at TEXT,
        UNIQUE (subject, idempotency_key)
    );

    CREATE TABLE publication_events (             -- committed history; enumerated instead of revisions
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        catalogue_sequence INTEGER NOT NULL UNIQUE,
        kind TEXT NOT NULL CHECK (kind IN ('publish', 'archive', 'restore')),
        document_id TEXT NOT NULL,
        revision_id TEXT,
        stored_artifact_sha256 TEXT,
        subject TEXT,
        occurred_at TEXT NOT NULL
    );
    CREATE INDEX events_by_document ON publication_events (document_id, event_id);

    CREATE TABLE derived_state (                  -- search/relationship generations (B06)
        name TEXT PRIMARY KEY,
        generation_sequence INTEGER NOT NULL,     -- catalogue sequence the snapshot was built from
        state TEXT NOT NULL CHECK (state IN ('building', 'ready', 'failed')),
        updated_at TEXT NOT NULL
    );

    CREATE TABLE relationship_generations (
        generation_id TEXT PRIMARY KEY, catalogue_sequence INTEGER NOT NULL, input_vector_sha256 TEXT NOT NULL,
        rule_version TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('building', 'ready', 'failed')),
        created_at TEXT NOT NULL, completed_at TEXT, error TEXT
    );
    CREATE TABLE detected_relationships (
        relationship_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL,
        source_document_id TEXT NOT NULL, source_revision_id TEXT NOT NULL, source_object_id TEXT NOT NULL,
        target_document_id TEXT NOT NULL, target_revision_id TEXT NOT NULL, target_object_id TEXT NOT NULL,
        kind TEXT NOT NULL, confidence TEXT NOT NULL, evidence TEXT NOT NULL, rule_version TEXT NOT NULL
    );
    CREATE INDEX detected_by_source ON detected_relationships (generation_id, source_document_id);
    CREATE INDEX detected_by_target ON detected_relationships (generation_id, target_document_id);
    CREATE TABLE manual_relationships (
        relationship_id TEXT PRIMARY KEY,
        source_document_id TEXT NOT NULL, source_revision_id TEXT NOT NULL, source_object_id TEXT,
        target_document_id TEXT NOT NULL, target_revision_id TEXT NOT NULL, target_object_id TEXT,
        kind TEXT NOT NULL, reason TEXT NOT NULL, creator_subject TEXT, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, etag TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'needs_review', 'archived_target', 'deleted'))
    );
    CREATE TABLE relationship_audit (
        audit_id INTEGER PRIMARY KEY AUTOINCREMENT, relationship_id TEXT NOT NULL, action TEXT NOT NULL,
        subject TEXT, occurred_at TEXT NOT NULL, before TEXT, after TEXT
    );
    """),
]


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                 "description TEXT NOT NULL, applied_at TEXT NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection, now: str) -> list[int]:
    """Apply pending migrations; returns the versions applied. Safe to call on every start.

    `conn` must be in autocommit mode (isolation_level=None).
    """
    applied = []
    have = current_version(conn)
    known = max(v for v, _, _ in MIGRATIONS)
    if have > known:
        raise RuntimeError(f"catalogue schema {have} is newer than this library ({known}); upgrade the library")
    for version, description, sql in MIGRATIONS:
        if version <= have:
            continue
        # One explicit transaction per migration (the connection runs in autocommit mode).
        script = (f"BEGIN IMMEDIATE;\n{sql}\n;INSERT INTO schema_migrations VALUES "
                  f"({int(version)}, '{description.replace(chr(39), chr(39) * 2)}', '{now}');\nCOMMIT;")
        try:
            conn.executescript(script)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied
