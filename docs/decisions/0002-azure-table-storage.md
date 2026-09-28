# ADR 0002: Azure Table Storage for the Azure catalogue (B10)

Status: **accepted for the pilot.** It is proven against Azurite. A live Azure run (A15) is
still needed before it is used with real data.

## Context

Handoff 5 and 17.2 name Azure Table Storage for catalogue and job rows, and Blob Storage for
artifacts. Both are provisional until B10 proves the section 17 commit and recovery rules
under crash and concurrency tests and compares costs. The two options are the stated choice
or "an available transactional backend without weakening the contracts".

## What the design needs, and what Table Storage gives

| Need | Table Storage feature used |
|---|---|
| One atomic publication commit: pointer, sequence and event together | Entity-group transaction in the single `documents` partition (≤100 operations; we use ≤6) |
| No lost updates on the current pointer | Conditional replace with the entity ETag (If-Match) on the document row and the `state` row |
| One stream per (asset, type, environment, scope) | A `stream:{hash}` row created in the same transaction; creation fails if it exists |
| Idempotency keys that survive races | `key:{hash}` row created together with the import row |
| Immutable artifacts | Blob upload with `overwrite=False` (If-None-Match: *) |
| Durable dirty marker for derived state | `state.sequence`, advanced only inside the commit transaction |

Nothing depends on cross-partition atomicity. Revision descriptors, blobs and import rows
are written outside the commit. The `evrev:{revision}` row, written inside the commit, is the
authoritative record that a revision committed. Reconciliation uses it to repair an import
whose final update was lost.

## Evidence

- **Contract suite:** `apps/library/tests/test_store.py` runs the same 17 contract tests
  three ways:
  - LocalStore;
  - AzureStore over in-memory Table/Blob semantics;
  - AzureStore over the real Azure SDKs against Azurite (CI job `azurite`).

  The tests cover publication, ETags, duplicates, idempotency, stream identity, crashes at
  every commit stage, intervening commits, archive/restore, pagination and integrity checks.
- **Semantics:** `test_azure_backend.py` checks against Azurite that the in-memory emulation
  matches the service: create-if-absent, stale-ETag rejection, all-or-nothing partition
  transactions, prefix queries, and blob no-overwrite.
- **Stress:** four replicas race on one ETag for six rounds, with crashes injected at
  `after_prepare`, `before_commit` and `after_commit` (three rounds against Azurite). The
  invariants hold every time:
  - exactly one winner per ETag;
  - the event sequence has no gaps or duplicates;
  - every committed revision is readable and integrity-checked;
  - no import reports a success that was not committed;
  - none is left prepared after recovery.

## Cost comparison

`apps/library/tests/measure_azure_ops.py` counts storage operations per action:

| Action | Table operations | Blob operations |
|---|---|---|
| Publish a new document | 25 | 1 write |
| Publish a new revision | 23 | 1 write |
| Retry with the same key | 3 | 0 |
| Duplicate bytes | 8 | 0 |
| Get a document | 1 | 0 |
| List documents (50) | 1 | 0 |
| List revisions | 3 | 0 |
| Read an artifact | 2 | 1 read |
| Archive / restore | 4 | 0 |

Worked pilot example, with assumptions to replace by the client's figures:

- 500 documents, 2 new revisions each per month (1,000 publications);
- 50,000 document reads and 20,000 artifact views per month;
- artifacts of 2 MB median (the measured real-report median is 1.7 MB), 12 revisions kept,
  so about 12 GB.

Estimated usage:

- Table transactions ≈ 1,000 × 25 + 50,000 × 2 + 20,000 × 2 ≈ 165,000 per month.
- Blob ≈ 1,000 writes and 20,000 reads per month.
- Storage ≈ 12 GB of blobs; catalogue rows are well under 1 GB.

At Standard LRS list prices of the order of US$0.0004 per 10,000 table transactions and
about US$0.05/GB-month for table data, plus about US$0.02/GB-month for Hot blob data with
cents per 10,000 blob operations, this pilot comes to **well under US$1 per month in
storage**. The total is dominated by blob capacity. Prices vary by region and change: check
them in the Azure pricing calculator for the client's region before relying on these figures
(section 16 costing worksheet, B13).

Transactional alternatives, for the same pilot:

- **Azure SQL Database, serverless:** billed per vCore-second while active, plus storage.
  Auto-pause helps, but the first request after a pause waits for the database to resume.
  Its minimum monthly cost is typically several dollars or more.
- **Azure Database for PostgreSQL Flexible Server (Burstable B1ms):** a fixed monthly
  compute charge, typically around US$10–20, plus storage. It is always on.
- **Cosmos DB serverless:** per request unit and per GB. It has transactional batches within
  a logical partition like Table Storage, but costs more per operation for the same shape.

Table Storage is the cheapest option that meets the contract. It needs no server, uses the
same storage account as the blobs, and works with managed identity.

## Consequences and limits

- Queries are per partition. Listing documents reads the `documents` partition's `doc:`
  rows, and reconciliation reads all `imp:` rows. That is fine at pilot scale (hundreds to low
  thousands of documents). Scale-out needs load testing (handoff 5) and probably
  per-document indexes or an import TTL cleanup.
- All commits serialize on the `state` row ETag. Contention retries up to 20 times, then
  returns `503 COMMIT_CONTENDED`. That suits one team and one replica (the pilot allows at most one).
- Table entities are limited to 1 MB and each string property to 64 KiB. Rows hold only small
  catalogue fields; large payloads stay in blobs.
- **A15 is not done.** Setting `BIDOC_AZURE_TEST_CONNECTION_STRING` to a disposable storage
  account runs the same suites live. That needs the owner's authorization and resources
  (handoff: no billable resources without authorization).
