# Library storage and publication (B04)

Local backend: `apps/library/bidoc_library/store.py` (`LocalStore`), standard library only (SQLite + filesystem).

## Layout

```
<LOCAL_DATA_DIR>/
  catalogue.sqlite3                                   documents, revisions, imports, events, relationships
  artifacts/<document_id>/<revision_id>/document.html immutable stored artifacts (never overwritten)
  staging/                                            temporary files; readers never look here
```

Paths are built only from validated UUIDs, never from titles or client file names. Keep the folder on a local persistent disk: **one library instance per folder**, and never on network or object storage (handoff section 3). Azure uses its own adapter (B10).

## Publication

1. **Idempotency.** Keys are scoped to (subject, key). Replaying the same request returns the stored outcome. The same key with a different artifact, target or precondition is `409 IDEMPOTENCY_KEY_REUSED`.
2. **Byte duplicates.** Identical submitted bytes return the original outcome, marked `duplicate: true`, and never move the current pointer. A duplicate of a conflicted revision returns the conflict.
3. **Validation** of the submitted artifact, including its hash (`422 CONTRACT_INVALID`, `413` over the limit).
4. **Identity checks:**
   - document type and stream tuple (asset, environment, scope) are immutable (`409`);
   - updating an existing document requires `If-Match` (`428 PRECONDITION_REQUIRED`), and a stale ETag is `409 REVISION_CONFLICT`;
   - a known revision ID with different bytes is `409 REVISION_BYTES_CONFLICT`.
5. **Re-projection** (`bidoc_engines.convert.reproject`). The shared projection is applied again and the document re-rendered with the trusted renderer; the producer's labels are never trusted. Withheld code stays withheld. An unsupported native schema is `422 UNSUPPORTED_SAFE_PROJECTION`. Only the regenerated artifact is stored; the submitted bytes are kept as a digest only.
6. **Immutable write**, create-if-absent (hard link from staging, fsync).
7. **Prepared revision**, with the original expected ETag.
8. **Commit, in one SQLite transaction:** re-check the ETag and stream; update the document pointer; advance `catalogue_sequence`; write the publication event; mark the revision and import committed. The response carries `catalogue_sequence` and `indexing_state: pending`; search and relationships catch up in B06.

## Crash recovery

| Crash point | State left | On restart |
|---|---|---|
| after artifact write | unreferenced file; import `validating` | import `failed IMPORT_INTERRUPTED` (retry with a new key); file removed |
| after prepare / inside commit | revision `prepared` | committed under its **original** ETag, or `conflicted` if another revision was committed first. A conflicted revision is never promoted and never appears in history |
| after commit | committed | nothing to do; a retry with the same key returns the committed outcome |

`reconcile()` finishes prepared imports whenever a retry needs them. Startup additionally cleans interrupted imports, unreferenced artifacts and staging files, but only when they are older than `cleanup_grace_seconds` (default one hour). A second process opening the same folder therefore cannot delete a publication still in flight.

## Reads

- **Documents and revisions:** only committed revisions are readable. History is enumerated from publication events, and every artifact read is checked against its stored SHA-256 (`500 ARTIFACT_CORRUPT` on mismatch).
- **Archive/restore:** needs `If-Match`, advances the sequence and records an event.

## Backup and restore (A14)

```python
LocalStore(data_dir).backup("/backups/2026-09-28")   # SQLite online backup + every committed artifact, checksum-verified
LocalStore("/backups/2026-09-28")                    # open the copy directly, or copy it back into place
```

- Stop publishing before a file-level copy of a live folder; `backup()` is safe while running.
- Restore by pointing `LOCAL_DATA_DIR` at the backup, or copying it back. Opening the store applies pending migrations; back up first.

## Migrations

- Versioned scripts live in `bidoc_library/migrations.py`. Each runs once in its own transaction and is recorded in `schema_migrations`.
- A catalogue created by a newer library is refused rather than downgraded.
