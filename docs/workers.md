# Processing workers and jobs (R3, B14)

R3 is optional. Without it, people document PBIX files with the generator on their own
computer, and nothing here changes that. With it, a publisher can upload a PBIX or a
PBIP project ZIP to the library, and an enrolled Windows worker processes it with the
same engines. The upload screen itself is B15; this page covers the protocol and the
worker.

## Enrolling a worker

1. An administrator enrolls the worker. The token is shown once:

   ```sh
   POST /api/v1/workers  {"label": "build-01", "input_types": ["pbix", "pbip_zip"]}
   -> 201 {"worker_id": "...", "token": "bidocwk_<id>_<secret>", ...}
   ```

2. On the Windows machine, with pbi-tools configured (`bidoc config`), run:

   ```sh
   bidoc worker connect https://docs.example.com      # paste the token
   bidoc worker run                                   # heartbeat, claim, process; Ctrl+C stops
   ```

   The token is kept in Windows Credential Manager (a file only the owner can read
   elsewhere). `bidoc worker run --once` processes at most one job.

**Revoking a worker:** `DELETE /api/v1/workers/{id}`. Its token stops working at once, and
any job it held goes back to the queue.

**What the worker runs:** only the pbi-tools that an administrator configured. Nothing is
discovered by scanning the disk. The worker connects outbound only, to
`/api/v1/worker/*`, with its own token.

## Jobs

| Browser route (publisher) | Result |
|---|---|
| `POST /jobs` (multipart `file`, `input_type` = `pbix` or `pbip_zip`, optional `document_id`) | `202 {job_id, state: "queued"}`; `409 WORKER_UNAVAILABLE` when no ready worker supports the input |
| `GET /jobs`, `GET /jobs/{id}` | the requester's jobs (an admin sees all): state, stage, attempt, error, output document and revision |
| `POST /jobs/{id}/cancel` | `202`; `409 JOB_TERMINAL` when it already ended (after publication: `outcome: too_late`) |
| `POST /jobs/{id}/retry` | `202` for a failed or cancelled job whose source is still kept; `409 JOB_NOT_RETRYABLE` otherwise |

**States:**

- queued → leased → running → publishing → succeeded
- queued → cancelled
- leased or running → cancel_requested → cancelled
- leased, running or publishing → failed

Stages (downloading, extracting, analysing, rendering, uploading) show progress without
changing state.

| Worker route (`/api/v1/worker`, worker token) | Contract |
|---|---|
| `POST /heartbeat` | versions, input types and readiness; `204`. Ready for 90 s after a ready heartbeat; the worker sends one every 30 s |
| `POST /claim` | `200 {job, lease_token, lease_expires_at, input_download_url}` or `204` |
| `GET /jobs/{id}/source` | the source, streamed; the lease token goes in `X-Lease-Token`, never in the URL |
| `POST /jobs/{id}/renew` | `200 {lease_expires_at, cancellation_requested}` or `409 LEASE_STALE` |
| `POST /jobs/{id}/progress` | a stage; `204` or `409` |
| `POST /jobs/{id}/results` | stage an immutable candidate for this attempt: `201 {staged_result_id}` or `409`. Nothing is published |
| `POST /jobs/{id}/complete` | the server publishes the staged result as the requesting user, under the current lease; `200` with the committed outcome, or `409` |
| `POST /jobs/{id}/fail` | error code, message and whether it is retryable; `200` with the resulting state |

The worker routes live under their own prefix, not the handoff's `/workers/{id}/…` and
`/jobs/{id}/…`. The token already identifies the worker, and one prefix lets an ingress
exempt the whole token-only namespace from browser sign-in, as it does for publishing
(`deploy/azure/main.bicep`).

## Guarantees

- **The job row is authoritative;** there is no separate queue. A claim is a
  compare-and-set on it, so of several workers claiming at once, exactly one wins.
- **Leases** last 120 s and are renewed every 30 s. An expired lease requeues the job
  while attempt < 3, and otherwise fails it with `LEASE_EXPIRED`. A manual retry allows
  three more attempts.
- **Publication:**
  - Only the server publishes. The publication commit re-reads the job row and checks its
    lease. For LocalStore this is the same SQLite transaction. For Azure, the job row lives
    in the commit partition, so the commit carries the job row's ETag, and a renewal or
    reassignment in between makes the commit retry and fail the check.
  - The same commit marks the job succeeded. A stale worker (A39) therefore cannot
    publish, and a job publishes at most once (A16).
  - Retrying `complete`, even with different candidate bytes, returns the persisted
    outcome.
- **Cancellation:** cancelling before the commit prevents publication, and the worker
  kills the extraction process tree and removes its workspace (A17). After the commit,
  the job stays succeeded and the cancellation is reported as too late.
- **Retention:**
  - Raw sources and staged candidates are deleted `SOURCE_RETENTION_HOURS` (24) after the
    job ends, by `python -m bidoc_library cleanup`. The library runs it on start, and the
    Azure template runs it daily as a Container Apps job, so it does not depend on
    traffic.
  - For a local library, schedule the same command daily.
  - Error details keep no raw content. Backups exclude sources and candidates.
- **Identity:** each job runs in a fresh workspace. Without `document_id`, a job creates a
  new document. To publish a new version of an existing document, pass its `document_id`.
- **Uploads** are streamed to storage and capped by `MAX_SOURCE_BYTES` (1 GiB). Project ZIPs
  are extracted inside the job's workspace with safe-path and size checks (no traversal,
  absolute paths, drive letters, symlinks or encryption; at most 20,000 entries and 4 GiB),
  and the project's sibling folders are kept.

## Tested

- `apps/library/tests/test_jobs.py` runs on LocalStore, in-memory Azure and Azurite. It
  covers:
  - availability (A16: no ready worker means processing is unavailable, while browsing
    still works);
  - the token boundary and revocation;
  - one winner among concurrent claims;
  - publishing once, as the requester;
  - a killed worker's lease recovering;
  - a stale worker unable to publish, including at the store commit boundary (A39);
  - renewal, attempts running out and manual retry;
  - retryable and permanent failures;
  - cancellation before and after publication;
  - retention.
- `apps/generator/tests/test_worker.py` runs a real library server with the worker. It
  covers:
  - a PBIX job through the pbi-tools stand-in;
  - a PBIP project ZIP through `bidoc worker connect` and `run --once`;
  - bad input and extraction failures;
  - A17: cancelling mid-extraction kills the process tree and removes the workspace, and
    the next job runs.

  It runs on Linux and on Windows CI.

**Not yet verified (B16):** real unattended PBIX extraction with Power BI Desktop under the
intended account, after a reboot, with the session locked or logged off. Until then, the
local generator is the supported route for PBIX.
