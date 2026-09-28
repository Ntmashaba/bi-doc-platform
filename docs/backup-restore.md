# Backup and restore (B13; A14, A28)

```sh
python -m bidoc_library backup DEST      # into a new, empty folder
python -m bidoc_library verify DEST      # check every file against its checksum
python -m bidoc_library restore DEST     # into the configured library, which must be empty
python -m bidoc_library check            # read every committed revision back
```

The commands use the same environment configuration as the server (`DATA_BACKEND`,
`LOCAL_DATA_DIR`, `AZURE_STORAGE_*`, and so on). On Azure, run them from a machine or job
whose identity has the storage data roles. The deployed app's identity, or an operator
with *Storage Table Data Contributor* and *Storage Blob Data Contributor*, both work.

## What a backup holds

A backup is the committed catalogue at one sequence, plus every immutable file it refers
to:

- documents, every revision, publication events and history;
- metadata overrides and their audit;
- manual relationships, their audit and tombstones;
- search and relationship generation pointers, and every pinned generation, so historical
  links keep their exact evidence;
- publishing token records (hashes only, so revocations survive) and installer releases;
- `backup.json`: format `bidoc-backup/1`, backend, library version, date, catalogue
  sequence, counts, and every file with its SHA-256 and size.

It does not hold:

- the local session secret (a new one is made);
- staging uploads and partial files;
- processing-job sources and staged candidates (raw PBIX or project content, kept at most
  24 hours). Job records are kept, and a restored job whose source is gone cannot be
  retried.

**Consistency:**

- **Local:** the catalogue is copied with SQLite's online backup API, so the library can
  keep serving. Files are immutable and written before their catalogue commit, so every
  referenced file already exists when it is copied.
- **Azure:** the table is read after noting the catalogue sequence, and blobs are listed
  after the table scan (a superset). If the sequence moved during the scan, the backup
  retries, up to 5 times, and then asks you to run it when the library is quieter.
- **Both:** the backup refuses to finish if any committed revision's artifact is missing
  or fails its checksum.

## Restore

A restore goes into an **empty** deployment of the **same backend**: a new data folder, or
a new table and container. It never merges into a library that already has data.

1. It verifies every file against `backup.json`, and refuses a damaged backup or one with
   unexpected files.
2. It writes the catalogue and files.
3. It opens the restored library and reads every committed revision back through its
   checksum. The result must match the recorded catalogue sequence.

Derived search and relationship snapshots are restored as they were. If anything is stale,
the library rebuilds on start. Pinned generations are never rebuilt, so historical
evidence stays exactly as it was.

To move between backends (local to Azure), import the documents again. It is not a
restore.

## Retention and schedule

- Run a backup before every upgrade, plus on a schedule suited to how often people
  publish. Daily is enough for a pilot.
- Keep several backups, and keep them somewhere other than the library's own storage
  account.
- Nothing in the library prunes referenced revisions or generations, so any retained
  backup restores completely.
- The Azure template also enables 14-day soft delete for blobs and containers. That
  guards against operator mistakes; it is not a backup.

## Tested

`apps/library/tests/test_backup.py` runs on LocalStore, in-memory Azure semantics and
Azurite (in the CI `azurite` job). It covers:

- backup and restore into a clean deployment, after which every reader-visible result is
  identical: catalogue, history, artifact bytes, metadata history, current and pinned
  relationship generations, manual assertion audit, and the search index (A14, A28);
- damaged files, extra files, occupied targets and the wrong backend are refused;
- the command line: backup, verify, restore and check.
