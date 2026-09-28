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

## Not yet (B10b)

- The library app does not serve from Azure yet. `DATA_BACKEND=azure` is still refused.
- Still to move to Azure before it does:
  - derived search snapshots and relationship generations (immutable blobs plus pointer rows
    in `documents`);
  - manual relationship assertions and their audit (in the commit partition);
  - managed-identity configuration (`AzureTables.from_identity`, `AzureBlobs.from_identity`
    exist but are not wired up).
- Backup and restore for Azure is B13.
