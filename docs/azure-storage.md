# Azure storage backend

`bidoc_library.azure.AzureStore` has the same contract as `LocalStore`, backed by Azure
Table Storage (catalogue) and Blob Storage (artifacts). The design decision and its evidence
are in `docs/decisions/0002-azure-table-storage.md`.

## Layout

One table and one blob container.

| Partition | Rows |
|---|---|
| `documents` (the commit partition) | `state` (catalogue_sequence); `doc:{id}` (current pointer, catalogue fields, metadata overrides, logical ETag); `stream:{hash}`; `event:{seq}`; `evrev:{revision}`; `docev:{doc}:{seq}`; `meta:{doc}:{seq}` (override audit) |
| `revisions:{doc}` | `rev:{revision}` (prepared descriptor: digests, sizes, catalogue fields) |
| `imports` | `imp:{import}`, `key:{hash(subject, key)}`, `sha:{submitted digest}` |

Blobs are stored at `artifacts/{document_id}/{revision_id}/document.html`, created if absent
and never overwritten.

## Publication

1. Record the import and its idempotency key together (partition `imports`).
2. Admit and re-project the artifact. This is the same code as the local store
   (`bidoc_library/admission.py`).
3. Write the blob, create-if-absent. Existing different bytes give `409 REVISION_BYTES_CONFLICT`.
4. Write the revision descriptor, and set the import to `prepared`.
5. **Commit.** One transaction in `documents` holds:
   - the `state` row (If-Match), advancing the sequence;
   - the `doc` row (If-Match, or created, together with its `stream` row);
   - the `event`, `evrev` and `docev` rows.

   A failed condition re-reads and decides: already committed; conflicted (the document
   changed, or the stream is taken); or retry (only the sequence moved).
6. Mark the import and descriptor committed. If this is lost, `evrev` still records the
   outcome, and a retry with the same key or the next reconcile repairs the import.

At startup, prepared imports are committed or conflicted. Imports still validating after the
grace period (default 1 hour) are marked `IMPORT_INTERRUPTED`, and their uncommitted blob
and descriptor are removed.

## Running the tests against Azure Storage

```sh
docker run -d -p 127.0.0.1:10000:10000 -p 127.0.0.1:10002:10002 mcr.microsoft.com/azure-storage/azurite \
  azurite --blobHost 0.0.0.0 --tableHost 0.0.0.0 --skipApiVersionCheck --loose
export BIDOC_AZURE_TEST_CONNECTION_STRING="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
cd apps/library && python -m unittest discover -s tests -p "test_store.py" && python -m unittest discover -s tests -p "test_azure_backend.py"
```

(That is Azurite's published development key, not a secret.) With a connection string for a
**disposable** real storage account, the same command is the live A15 run. Each test
creates and deletes its own uniquely named table and container.

## Serving the library from Azure (B10b)

`DATA_BACKEND=azure` serves the whole API from Table and Blob storage:

- **Catalogue:** as described above.
- **Derived state:**
  - Search snapshots are immutable blobs (`derived/search-{seq}-{id}.json`).
  - Relationship generations are immutable blobs (`derived/generations/{id}.json`), each
    listing its revision vector, detected links and the manual assertions pinned to it.
    Per-revision index rows (partition `generations`, newest first) find the latest
    generation containing a revision.
  - The pointers (`derived:search`, `derived:relationships`) and the generation's `gen:{id}`
    row switch in one `documents` transaction that also re-writes the `state` row under its
    ETag. The switch happens only if the catalogue sequence has not moved, so a stale rebuild
    never replaces a newer one. A generation without a `gen:` row was never switched in, and
    is never served.
- **Manual links:**
  - Records (`manual:{id}`) and audit rows (`maudit:{id}:{seq}`) commit with the `state`
    row, advancing the sequence.
  - Validation runs against the sequence it read, and the write is refused if the sequence
    moved. `manual.py` then re-reads and re-validates, so a link is never created against a
    stale selection.
- **Metadata overrides:** on the document row, audited in `meta:{doc}:{seq}`, as described
  above.

Derived state and manual links use one repository interface (`bidoc_library/repository.py`)
that both stores implement. `derived.py` and `manual.py` contain no storage code.

**Evidence:** every API-level suite runs three times: LocalStore, in-memory Azure semantics
and Azurite. That covers search, detected and manual relationships (including pinned
historical generations and stale-rebuild protection), metadata overrides, legacy import,
documents, and access modes. It is 167 library tests with Azurite configured, and they run in
the CI `azurite` job.

```sh
DATA_BACKEND=azure AUTH_MODE=gateway GATEWAY_TRUSTED_PROXIES=10.0.0.0/16 \
AZURE_STORAGE_TABLE_ENDPOINT=https://<account>.table.core.windows.net \
AZURE_STORAGE_BLOB_ENDPOINT=https://<account>.blob.core.windows.net \
python -m bidoc_library
```

The identity needs the **Storage Table Data Contributor** and **Storage Blob Data
Contributor** roles on the account. Deployment templates and the runbook are B13.

## Still not done

- **Live Azure (A15):** needs authorization and a disposable account; the same suites run
  there unchanged.
- **Backup and restore for the Azure backend:** B13.
- **Scale:** listing and reconciliation read whole partitions; load testing comes before
  scale-out (ADR 0002).
