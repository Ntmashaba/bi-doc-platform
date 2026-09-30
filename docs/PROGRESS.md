# Implementation progress

Living record of work against `docs/Power-BI-Platform-Agent-Handoff.md`. Newest entries at the bottom of each section.

## Backlog status

| ID | Status | Notes |
|---|---|---|
| B01 | Done except Windows packaging spike (blocked: no Windows host) | Findings: `docs/b01/BASELINE.md`; scripts: `spikes/b01/` |
| B02 | Done | Envelope v1, scope keys, projection and identity specs frozen (pre-release): `docs/contracts/` |
| B03 | Done (PBIX via bidoc deferred to B08) | Engines 0.2.0 fixes; `packages/engines` adapters, projection, identity; `bidoc` CLI; real-input checks in CI |
| B04 | Done | `apps/library` LocalStore: SQLite catalogue, immutable revisions, crash-safe publication (`docs/library-storage.md`) |
| B05 | Done | HTTP API v1 (`docs/library-api.md`, `docs/openapi-v1.json`); local and gateway access |
| B06 | Done | B06a backend (`docs/relationships.md`); B06b shell and viewers (`docs/library-ui.md`) |
| B07 | Done (R1 gate; A08 → B08, A10 ZIP → B12) | Evidence map: `docs/b07/R1-GATE.md`; Docker: `docs/deployment-docker.md` |
| B08 | Done (real PBIX on Windows is W2, owner-run) | `docs/generator.md`: PBIX extraction, batches, history, desktop app |
| B09 | Done on Windows CI (30/30); clean-machine A13 run waiting on owner | `docs/b09/VERIFICATION.md`; `packaging/windows/` |
| B10 | Done against Azurite (the library serves from Azure storage); live Azure A15 needs authorization | `docs/azure-storage.md`, ADR 0002 |
| B11 | Done (A36 through a TLS ingress in B13) | `docs/publishing.md` |
| B12 | Done (A12 in Edge on Windows CI) | `docs/portable-export.md`; ZIP: `docs/contracts/envelope-v1.md` |
| B13 | Done in CI; owner steps listed in the R2 checklist | `docs/azure-deployment.md`, `docs/backup-restore.md`, `docs/performance.md`, `docs/release-checklist-r2.md` |
| B14 | Done (real unattended PBIX extraction is the B16 gate) | `docs/workers.md` |
| B15 | Done | `docs/library-ui.md` (Process PBIX, Settings), `docs/workers.md` |
| B16 | Checklist and evidence script ready; waiting on the owner's Windows run | `docs/b16/CHECKLIST.md`, `packaging/windows/b16/b16_gate.py` |
| W1 | Probe passed | Windows CI builds and runs a PyInstaller + pywebview exe. Full packaging of the generator is B09 |
| W2 | Waiting on owner | Real PBIX extraction on the owner's Windows machine with Power BI Desktop + pbi-tools |

## Log

### 2026-09-28 — B01 started
- Branch `claude/nifty-thompson-56taho`.
- Engines pinned for review: pbi-doc-gen `a7d5565cc1d237f8efc4dc96e47b756d15c1b14a`, adf-doc-gen `7c8cfe501b9d1e79eb53478779056ef9f1c9d329`.
- Ran both suites: pbi-doc-gen 163 OK, adf-doc-gen 26 OK (Python 3.11.15, Linux).
- Generated synthetic samples; measured HTML/payload/search-term sizes (real PBIX sizes unmeasured — needs Windows).
- Safe-projection probes: all 5 seeded PBI markers leak; PBI embeds absolute `model.sourcePath`; ADF redacts credentials but leaks SQL query literals.
- Endpoint probe: SQL port collision and SQL case-folding happen in the ADF engine (`common.py`), not only the bridge; Delete is emitted as a sink.
- Identity survey: no explicit object IDs in either engine; PBI `lineageTag` is the lead to investigate in B02.
- Legacy import spike: native payload recoverable from existing HTML without script execution.
- Windows packaging spike: **not run** (no Windows host) — explicit open gate.

### Owner decisions (resolved — see `docs/decisions/0001-b01-owner-decisions.md`)
1. Query code: local keeps raw M/SQL; shared publication has an explicit *Include query code* option, off by default, removing code from payload and search index when off.
2. pbi-tools: separate prerequisite for R1. Licence verified upstream (main and tag 1.2.0): AGPL-3.0.
3. Windows: W1 packaging build on Windows CI and W2 real PBIX extraction on the owner's machine, reported separately.
4. Engines get `pyproject.toml`; platform consumes versioned engine packages.

### 2026-09-28 — Owner decisions applied; engine packaging; W1 probe
- ADR 0001 recorded. pbi-tools licence verified from upstream `LICENSE` at `main` and tag `1.2.0`: AGPL-3.0.
- Engine packaging PRs (pyproject, console script, templates as package data, wheel install test):
  pbi-doc-gen [#2](https://github.com/Ntmashaba/pbi-doc-gen/pull/2) (166 tests OK, `run_ci.py` OK with no skips);
  adf-doc-gen [#2](https://github.com/Ntmashaba/adf-doc-gen/pull/2) (29 tests OK). Engine CLI behaviour unchanged.
- W1 probe ([run](https://github.com/Ntmashaba/bi-doc-platform/actions/runs/36424226145)): `windows-latest` = Windows Server 2025 (10.0.26100),
  Python 3.11.9, WebView2 153.0; PyInstaller 6.22.3 + pywebview 6.2.1 build OK; frozen exe ran and read its bundled asset.
  **Not covered:** GUI window rendering (headless runner), installer tooling, engine generation inside the exe (B09).

### 2026-09-28 — B02 started
- `packages/contracts` (`bi-doc-contracts`, standard library only): envelope v1 JSON Schema, validator that interprets the schema file,
  manifest locate/embed/hash, strict JSON, size limits, cross-field and anchor checks.
- Golden fixtures built by `tests/build_fixtures.py` via the real `embed_manifest` (valid PBI, valid ADF with CRLF, escaped script-like
  text; invalid unsupported version, duplicate manifest, hash mismatch, missing anchor). 25 tests OK, including agreement with the
  reference `jsonschema` library.
- ADR 0001 policy reflected in the envelope: `projection.profile` (local/shared) and `projection.options.query_code` (included/withheld).
- Identity/binding draft `docs/contracts/identity-and-bindings-v1.md`. Found: TMDL reader and BIM path both drop `lineageTag` (B03 engine change).
- Added Linux CI for the contracts package (fails on skipped tests).

### 2026-09-28 — PRs merged; B02 completed
- Merged at the owner's instruction: bi-doc-platform #1, pbi-doc-gen #2, adf-doc-gen #2. Branches restarted from the new `main`.
- Scope keys: `bidoc_contracts.scope` derives `scope_key` from the descriptor; the validator rejects mismatches (29 contract tests OK).
- `docs/contracts/projection-v1.md`: shared projection with the code-field inventory found by marker seeding, always-on
  credential/URL/entered-data/machine-path rules, and the A29 gate. DAX stays (not query code).
- Snapshot rule: the envelope is attached only to a complete snapshot; ADF skipped files mean local-only output.
- Envelope, projection and identity specs marked frozen (pre-release).

### 2026-09-28 — B03 completed
- **Engine fixes** (PRs: pbi-doc-gen [#3](https://github.com/Ntmashaba/pbi-doc-gen/pull/3), adf-doc-gen [#3](https://github.com/Ntmashaba/adf-doc-gen/pull/3); both 0.2.0):
  - PBI keeps `lineageTag` (TMDL and BIM). Both engines escape `<`, `>` and `&` in the embedded `DATA`.
  - ADF keeps non-default SQL ports and SQL letter case in lineage keys, using the connector rule: SQL Server with no port = 1433.
  - The bridge: deletes are never producers; paths keep their case and encoding; a folder prefix is only `possible`.
  - Documented expectation change: the bridge test's `Enriched` goes from `exact` to `possible` (A22).
  - Suites: PBI 170 OK, ADF 34 OK. Also repaired the ADF `.gitignore` line I broke in the packaging PR.
- **Platform adapters** `packages/engines` (`bi-doc-engines`), with engines pinned by commit:
  - Power BI and ADF adapters: objects, bindings with raw and normalized endpoints, sections, navigation registry.
  - `shared-projection/1`, `endpoint-norm/1`, identity sidecar, and `generate(request, progress, cancellation)`.
- **`bidoc` CLI** (`apps/generator`): `generate` and `doctor`, with the handoff exit codes (`docs/generator.md`).
- **Tests:** contracts 29, engines 24, CLI 4, all OK.
- **Real inputs, also run in CI:**
  - 29 real Microsoft reports pre-extracted by pbi-tools: 58 valid artifacts; HTML median 1.7 MB (max 7.3 MB); search text median 5.4 KB (max 14.4 KB), inside the ~20.5 KB budget.
  - All 95 templates in Microsoft's Azure-DataFactory repository: 190 valid artifacts; HTML median 129 KB (max 550 KB).
  - No M/SQL in any shared artifact; local artifacts keep it.
- **Problems found and fixed during B03** (each now has a test):
  - Shared output leaked SQL that uses a function call in the select list, SQL embedded in activity `detail` via `pre-copy:`, SQL built in ADF expressions, and data-flow `query:` options.
  - The projection first over-withheld: DAX calculated tables (557 in the real reports) and partition source locations/descriptions (175). Those fields are now classified by content. DAX, prose and source locations stay in shared output.
  - The identity sidecar inside an ADF folder was read as factory input; it now lives beside the source.
  - The B02 Power BI scope mapping used the wrong engine mode names; corrected.
- **Deviation:** PBIX generation through `bidoc` is not wired (it needs Windows and pbi-tools to verify). `doctor` reports it as unavailable and points to `pbi-doc-gen --pbix`. Moved to B08.
- **Observation for the owner:** real reports reference source files under personal paths (e.g. `C:\Users\<name>\OneDrive…`). These are source locations, not generator machine paths, so shared output keeps them as documentation. Say if they should be treated as sensitive.

### 2026-09-28 — B03 PRs merged; B04 completed
- Merged at the owner's instruction: bi-doc-platform #2, pbi-doc-gen #3, adf-doc-gen #3. Engines are re-pinned to their merged `main` commits (pbi-doc-gen `86dd3fc`, adf-doc-gen `c66260b`).
- **`apps/library` (`bi-doc-library`), `LocalStore`** (`docs/library-storage.md`):
  - versioned migrations: catalogue, immutable revisions, imports, publication events, catalogue sequence, derived-state pointer, relationship tables (filled in B06);
  - publication: idempotency, byte-duplicate recovery, validate, identity/stream checks, re-project, immutable write, prepared revision, one atomic commit under the original ETag;
  - startup reconciliation; archive/restore; filtered, paginated reads; integrity-checked artifact reads; backup/restore.
- **`bidoc_engines.convert.reproject`:** every import is regenerated through the shared projection; producer labels are never trusted, and withheld code is never restored.
- **Tests:** library 19, engines 27, contracts 29, CLI 4, all OK. They cover A02, A04, A05 (concurrent writers: one wins, one 409), A06/A34 (crash after write, after prepare, inside commit, after commit; an intervening commit is never overwritten), A10 (invalid, unsupported, oversized: nothing stored) and A14 (backup/restore).
- **Found while testing:**
  - Startup cleanup would have deleted an in-flight publication if a second process opened the same folder. Cleanup now only touches work older than a grace period (1 h default), and a test covers it.
  - A test opening one store per thread exposed that risk. The library runs as one store per process; that rule is documented.

### 2026-09-28 — B04 merged; B05 completed
- Merged bi-doc-platform #3 at the owner's instruction.
- **HTTP API v1** (`apps/library/bidoc_library/api.py`, FastAPI):
  - routes: health, capabilities, documents (list/get/ETag), revisions, download, sandboxed view, imports (multipart, `Idempotency-Key`, `If-Match`, `query_code`), archive/restore, releases (honest 404);
  - error envelope with request IDs and no leaks; upload limits checked before parsing; committed OpenAPI checked in CI.
- **Access** (`access.py`), fail-closed:
  - `local`: loopback only, Host/Origin checks, per-installation session secret and CSRF header for changes;
  - `gateway`: identity trusted only from configured proxies, roles viewer/publisher/admin, CSRF header for changes;
  - `entra` and `azure` are refused until B13/B10.
- **Configuration** (`config.py`) is typed from the environment and refuses unsafe combinations, e.g. local mode on 0.0.0.0.
- **Locked dependencies:** `requirements-lock.txt` pins every third-party package, and `requirements.txt` uses it as a constraint.
- **Tests:** library 36 (17 API + 19 store), engines 27, contracts 29, CLI 4, all OK from a clean locked install.
- **Real server smoke test:** `python -m bidoc_library`, then publish with `curl` (201), read back, foreign Host (403), no secret (401).
- **Found:** unhandled 500s lacked `X-Request-ID` (answered outside the middleware); fixed and tested.
- **Deferred with reason:** legacy HTML import (no manifest) → B06/B07; metadata overrides → B06.

### 2026-09-28 — B05 merged; B06a (backend) completed
- Merged bi-doc-platform #4 at the owner's instruction. B06 is split in two for reviewability: B06a backend (this PR) and B06b frontend.
- **`packages/relationships`** (`rel-rules/1`):
  - exact_static and possible `produces`; `deletes` and `reads` kept apart;
  - contradictions reject a match (server, port, instance, database, account, container, known environment);
  - case-only differences, folder-contains-file and dynamic/opaque resolutions are `possible`. 10 tests (A20–A22, A30).
- **Derived state** (`derived.py`):
  - search snapshots and relationship generations, rebuilt synchronously and committed only at a still-current sequence;
  - generations pin revision vectors and manual assertion versions; historical links stay reproducible (A23, A33).
- **Search:** reference semantics (`search.py`), `GET /search-index` (ETag/304) and `GET /search`.
- **Relationships API:** objects and relationships per revision/generation/object; manual links with create/patch/delete, audit, `SELECTION_STALE`, `needs_review` (A24).
- **Migration 2:** generation members, pinned manual records, and detected rows keyed by generation. The upgrade from a B04 catalogue is tested.
- **Tests:** library 45, relationships 10, engines 27, contracts 29, CLI 4, all OK.
- **Scale (A41-style, 124 real documents):** index 580 KB (≈ 4.7 KB/doc), full rebuild 1.1 s, search 2.8 ms.
- **Found while testing:**
  - detected-relationship IDs clashed across generations (schema key fixed in the unmerged migration);
  - section ranking ignored heading matches (now heading first, as the handoff ranks);
  - manual-link status checked only one end.

### 2026-09-28 — B06a merged; B06b completed
- Merged bi-doc-platform #5 at the owner's instruction.
- **Engine PRs** (0.3.0; platform pinned to their branch commits):
  - pbi-doc-gen [#4](https://github.com/Ntmashaba/pbi-doc-gen/pull/4) (171 tests) and adf-doc-gen [#4](https://github.com/Ntmashaba/adf-doc-gen/pull/4) (35 tests);
  - both add a framed-only `bi-doc-viewer` protocol v1 listener (parent-only, channel + revision handshake, registered views);
  - ADF activity rows get stable IDs.
- **Library shell** (TypeScript, strict; compiled output committed):
  - All / Power BI / ADF with filters and browser search identical to the server's;
  - import with manifest preview (new / new version / duplicate) and an explicit *Include query code* option;
  - details and versions; archive/restore;
  - sandboxed viewer with object selector and the Related documentation panel (groups, confidence, evidence, state, manual links).
- **Power BI source navigation** now carries server/database/object, so the viewer opens the exact source.
- **Tests:**
  - search parity Python ↔ browser (14 query/filter cases, accented and non-Latin text); shell CSP and whitelist;
  - Chromium acceptance through a real server: A19 browse/filter/search; A20 Power BI source → exact ADF activity inside the viewer → reverse link → Back; measure selection; A27 isolation (parent, cookies, fetch and top navigation blocked; forged messages ignored); import; no unexpected errors;
  - new CI job `frontend`.
- **Found and fixed:** overlapping renders could show a stale view (each render now swaps in only if it is still the latest navigation).

### 2026-09-28 — B06b merged; B07 (R1 gate) completed
- Merged pbi-doc-gen #4, adf-doc-gen #4 and bi-doc-platform #6 at the owner's instruction.
- **A37, published viewers read-only:**
  - engines 0.4.0 PRs (pbi-doc-gen and adf-doc-gen #5) render report and factory details as text when `DATA.published` is set, with no download-to-save;
  - the library's `reproject` sets that flag on every stored copy;
  - local edits reach the library by regenerating.
- **A40, metadata overrides:**
  - migration 3 adds `revision_metadata`, `metadata_overrides` and `metadata_audit`;
  - new routes `GET`/`PATCH /documents/{id}/metadata` and `/metadata/history`;
  - artifacts are never touched; the sequence advances; overrides survive new revisions; stream fields give `422 IMMUTABLE_FIELD`.
- **Legacy import:**
  - known engine HTML without a manifest is converted: one `DATA` literal is decoded without running scripts, the schema must be supported, the result is re-rendered as shared with query code withheld, and it gets a new stream unless a target is given;
  - unknown HTML gives `422 UNSUPPORTED_SAFE_PROJECTION`;
  - new `POST /imports/preview`; the import view now uses the server preview and asks older documents for catalogue details.
- **Docker:**
  - multi-stage image built from the lock file; non-root; `/data` volume; health check;
  - local mode inside a container requires `LOCAL_CONTAINER_BIND=published-on-host-loopback-only` and a loopback-only port publish;
  - `check_docker.py` (A03) passed here: restart and re-creation keep the document, the secret and search; foreign Host refused; healthy;
  - new CI job `docker`.
- **Real data (A01):** all 29 real reports published through the API in 44 s. In every report that has measures (26), searching a measure name reaches that measure's own section and viewer. Added to the `real-pbix-samples` job.
- **New test for A31:** a parameterised dataset used by two Copy activities binds A→B and C→D separately; adding a source keeps existing IDs.
- **Found and fixed:**
  - the image's runtime stage tried to re-resolve the git-pinned engines (it now installs the built wheels with `--no-deps` and runs `pip check`);
  - my first A01 check looked for measure sections in the wrong list.
- **Gate:** `docs/b07/R1-GATE.md` maps every R1 acceptance ID to its CI evidence.

### 2026-09-28 — B07 merged; B08 completed
- Merged pbi-doc-gen #5, adf-doc-gen #5 and bi-doc-platform #7 at the owner's instruction. Engines re-pinned to their merge commits before the platform merge; CI green on that head.
- **PBIX extraction** (`bidoc_generator/extract.py`):
  - pbi-tools runs as a child process with an argument array, never a shell, in a per-item workspace;
  - a timeout or a cancellation kills the whole process tree (POSIX process group; `taskkill /T /F` on Windows);
  - wrappers and pbi-tools.core are refused.
- **Engine support for PBIX:**
  - `generate()` takes `source_kind="pbix"` plus the extract; identity, label and hash stay with the PBIX;
  - the adapter now passes the real PBIX to the engine, so PBIR report definitions and custom visual names are read from it (previously a non-existent path).
- **Batch runner and history** (`batch.py`, `history.py`):
  - inputs are recognised by shape, and a lone `.pbip` pointer is refused;
  - items run one at a time with per-item states, cancel and retry;
  - SQLite history; items still running at the last exit are marked `interrupted` on the next launch; failed workspaces are kept for 7 days.
- **CLI:** new `bidoc batch` (exit 5 for a partial failure), `bidoc history`, `bidoc retry`, `bidoc desktop`, and `generate --kind pbix` (exit 3 with a diagnosis when prerequisites are missing).
- **Desktop app** (`bidoc_generator/desktop`, TypeScript UI):
  - a loopback FastAPI app with Host/Origin checks, a per-launch session secret and the CSRF header;
  - screens: start → review → live processing → open/cancel/retry, plus history and prerequisites;
  - pywebview window with native pickers; previews open in a separate window without the bridge; closing with work running asks first.
- **Tests:**
  - a fake pbi-tools (ok, fail, hang, and one that spawns a grandchild process);
  - A08: three items, one bad, then retry;
  - A17: cancel kills the grandchild, and the next item runs;
  - timeout, interrupted items, and A07 (PBIX unavailable while other inputs work);
  - CLI exit codes; desktop API security; Chromium acceptance of the desktop UI;
  - new CI job `generator-windows`, which runs the generator suite on Windows, including `taskkill` tree kill.
- **Not verified here:** real PBIX extraction with Power BI Desktop (W2, owner) and the pywebview window itself (W1 proved pywebview runs; the full installer is B09).

### 2026-09-28 — B08 merged; B09 started
- Merged bi-doc-platform #8 at the owner's instruction.
- **Windows build** (`packaging/windows/`):
  - PyInstaller spec producing `bidoc.exe` and a windowed `BI Documentation Generator.exe` in one folder, with engines, templates, schema, desktop UI and pywebview bundled;
  - exact build pins in `requirements-windows-build.txt`.
  - Verified here: the spec builds on Linux, and the frozen app runs a PBIP + ADF batch, serves the desktop UI, and `--check` reports pywebview.
- **Installer** (Inno Setup): per-user, fixed AppId for in-place upgrade, uninstall keeps user data unless chosen, labelled unsigned. `release.json`, `SHA256SUMS.txt` and release notes are generated.
- **Generator changes:**
  - `bidoc config --pbi-tools`, stored in `config.json` in the generator home, so upgrades keep it;
  - doctor checks the WebView2 runtime on Windows;
  - frozen builds report their bundled Python.
- **A13 script** `verify-install.ps1`: install → doctor → generate → desktop → upgrade → uninstall, from a shell with no Python on `PATH`; static inputs in `verify-inputs/`. Runs in the new CI job `windows-installer` (0.2.0 → 0.2.1 upgrade).

### 2026-09-28 — B09 merged; B10a (Azure storage adapter) completed
- **B09 closed:** the first run had 29 of 30 checks pass; the one failure was my script's `py.exe` check, since fixed. The next run passed 30 of 30. Merged bi-doc-platform #9 at the owner's instruction, and recorded the result in `docs/b09/VERIFICATION.md`.
- **`AzureStore`** (`bidoc_library/azure/`) has the same contract as LocalStore on Table Storage plus Blob:
  - one commit transaction in the `documents` partition (state and document rows If-Match; stream, event, evrev and docev rows created);
  - descriptors and blobs are written first; `evrev` is the authoritative commit record used to repair lost import updates;
  - metadata overrides and archive/restore advance the sequence in the same partition.
- **Shared admission** (`admission.py`): validation, legacy conversion and re-projection used by both stores.
- **Evidence:**
  - the 17-test store contract suite runs against LocalStore, in-memory Azure semantics and Azurite (real SDKs);
  - semantics tests prove the emulation matches Azurite;
  - a stress test with 4 racing replicas and random crashes per round (6 rounds in memory, 3 on Azurite) holds every invariant;
  - all of this runs in the new CI job `azurite`;
  - the operation counts per action feed the cost worksheet.
- **ADR 0002:** Table Storage accepted for the pilot (cheapest option that meets the contract; limits recorded).
- **Not done:** live Azure (A15, needs authorization and a disposable account); serving the library from Azure (B10b).

### 2026-09-28 — B10a merged; B10b completed
- Merged bi-doc-platform #10 at the owner's instruction.
- **Repository interface** (`repository.py`): derived state and manual links now use a small interface with optimistic writes that name the sequence they read. LocalStore implements it on its existing SQLite tables; `derived.py` and `manual.py` no longer contain storage code.
- **AzureStore** implements the same interface:
  - search snapshots and relationship generations are immutable blobs;
  - pointers and `gen:` rows switch in one `documents` transaction guarded by the `state` ETag, so a stale rebuild cannot win;
  - manual links and their audit commit with the sequence.
- **`DATA_BACKEND=azure`** is supported with managed identity (endpoints plus optional `AZURE_CLIENT_ID`) or a connection string. The Docker image now includes the Azure SDKs; I checked the built image serving from Azurite.
- **Tests:** every API-level suite (documents, access modes, search, detected and manual relationships, metadata, legacy import) runs on LocalStore, in-memory Azure and Azurite: 167 library tests pass with Azurite. The CI `azurite` job runs them.
- **Still open:** live Azure (A15, needs authorization); backup/restore for Azure (B13).

### 2026-09-28 — B10b merged; B11 completed
- Merged bi-doc-platform #11 at the owner's instruction.
- **Publishing tokens:**
  - scoped (`publish` only), 1–30 days, shown once, stored as a SHA-256 hash;
  - issue, list and revoke through browser identity with CSRF; admin revoke-subject for publisher-role removal;
  - every action audited; migration 4 locally, and a `tokens` partition on Azure.
- **`/api/v1/publishing/*`:**
  - token-only and fail-closed with one generic 401; HTTPS required outside loopback (`X-Forwarded-Proto` only from trusted proxies);
  - uses the same import path as browser imports; imports are private to the token's subject; safe readback of results;
  - a token reaches nothing else.
- **Releases:** admin upload with a server-checked SHA-256, separate approval, immutable versions, integrity-checked download. The library UI gets a Downloads page (with an honest unavailable state) and a Publishing tokens page.
- **Generator:**
  - `bidoc connect`, `publish` and `disconnect`; the token goes in Windows Credential Manager (an owner-only file elsewhere);
  - HTTPS-only URLs, redirects refused, stable idempotency keys with retries;
  - desktop Library screen and per-item Publish.
- **Found and fixed:** the generator missed the library's lowercase `etag` header, so new versions were refused with 428. It now reads headers case-insensitively.
- **Tests:** library publishing suites on all three backends; generator against a real library server; Credential Manager on Windows CI; browser steps for tokens and downloads.
- **Open:** A36 through an actual ingress (B13, with the templates).

### 2026-09-28 — B11 merged; B12 completed
- Merged bi-doc-platform #12 at the owner's instruction.
- **ZIP profile:**
  - `validate_zip` in the contracts package handles traversal, absolute and backslash paths, case collisions, symlinks, encryption, nested archives, unexpected entries, and the size and entry limits (enforced while reading);
  - the library imports ZIP uploads on all backends and in preview; assets are not kept yet, and a coverage warning says so;
  - UI and API accept `.zip`, and `MAX_ZIP_BYTES` sets the cap.
- **Portable export:**
  - `bidoc export-library` builds a `file://` folder: catalogue, search, related-object links, and a sandboxed viewer with Back support;
  - documents are re-rendered read-only from their manifests; relationships are pinned in the snapshot; partial exports show "Not included".
- **Engine fix (pbi-doc-gen 0.4.1):** a framed report no longer adds browser history entries when switching tabs, so Back leaves the document as expected. The platform pin moves to that commit.
- **Tests:** 34 ZIP contract tests; ZIP import on LocalStore and Azure; export unit tests; the offline browser check in Chromium and in Edge on Windows CI.

### 2026-09-28 — B12 merged; B13 completed
- Merged pbi-doc-gen #6 and bi-doc-platform #13 at the owner's instruction. The platform now pins pbi-doc-gen at the merge commit (`8d89ca5`).
- **Backup and restore (A14, A28):**
  - `python -m bidoc_library backup | verify | restore | check`, on both backends;
  - a backup holds one committed sequence: catalogue, history, metadata and manual audit, generation pointers and every referenced immutable file, all checksummed;
  - restore goes only into an empty deployment of the same backend, verifies first, then reads every revision back;
  - tests on LocalStore, in-memory Azure and Azurite show every reader-visible result identical after restore, including pinned generations.
- **`AUTH_MODE=entra`:**
  - Container Apps / App Service built-in authentication; the library reads `X-MS-CLIENT-PRINCIPAL` only from trusted proxies and only for the configured tenant;
  - app roles map to viewer, publisher and admin; the subject is the Entra object ID;
  - no principal is `401`, and no role is `403` unless a default role is configured.
- **Azure template** (`deploy/azure/main.bicep`):
  - Container Apps Consumption with 0–1 replicas and 0.25 vCPU / 0.5 GiB;
  - a user-assigned identity with data-plane roles only, on a storage account with keys disabled;
  - Easy Auth everywhere except health and the token-only publishing API;
  - Log Analytics retention and a budget alert.

  CI compiles and lints it, checks the pilot limits, and checks that its configuration is accepted by the library.
- **Runbook** (`docs/azure-deployment.md`): app registration and roles, deploy, go-live verification (trusted-proxy check, forged-header check, A36 on the real ingress), access management, cold start, cost worksheet, upgrades.
- **A36 in CI** (`check_ingress.py`):
  - nginx terminates TLS in front of the library in gateway mode;
  - `bidoc connect` and `bidoc publish` publish a document, retry it (duplicate), publish a new version against the ETag, and read results back;
  - the token is refused on browser routes, forged identity headers are ignored, and revocation stops publishing.
- **A41** (`check_large_documents.py`, `docs/performance.md`):
  - a 150-table / 1,500-measure model (23.9 MB artifact) and a 400-pipeline factory;
  - publish takes 16 s, the full rebuild 2 s and peak memory is 266 MiB; the index is 884 KB;
  - server search equals browser search.

  New server search mode: above `CLIENT_SEARCH_INDEX_BYTES`, the UI uses `/search`. A browser step covers it.
- **Found:** a large model is close to the 25 MiB default upload limit; `docs/performance.md` says when to raise it.
- **Owner steps** (`docs/release-checklist-r2.md`): code signing, W2, clean-machine A13, live Azure A15, deployment and A36 on the client's ingress, first production backup.

### 2026-09-28 — Personal paths in shared output (owner decision)
- **Owner decision:** keep full paths locally. In shared output, withhold personal
  locations without merging distinct sources, and keep shared locations and relative
  repository paths.
- **Implemented in the shared projection** (`docs/contracts/projection-v1.md`, *Personal
  paths*):
  - every string is covered, including code, labels, report location, `pbixSource` and ADF
    entity keys and endpoints;
  - each path is replaced with its file name and a stable 12-hex reference;
  - the endpoint path becomes `withheld:<ref>`, so source IDs are opaque, distinct and
    stable;
  - labels read "Budget.xlsx — personal location withheld (ref …)";
  - relationship rules refuse to match withheld paths.
- **Tests** (`packages/engines/tests/test_personal_paths.py`):
  - two `Budget.xlsx` files in different folders stay two sources;
  - IDs are stable across revisions;
  - UNC and SharePoint locations are kept;
  - local output is unchanged;
  - no relationship comes from a withheld path;
  - replacements are idempotent.
- **Real samples:** the 29 Microsoft reports contained personal paths. 112 are now withheld;
  all real-sample checks still pass.
- **Effect on existing links:** sources that had a personal path in their ID get a new ID on
  their next shared publication. Manual links to them show *Needs review*. Nothing has been
  rolled out to a team yet.

### 2026-09-28 — B13 merged; personal-path follow-ups (owner review)
- Merged bi-doc-platform #14 at the owner's instruction.
- **Owner review:** the unkeyed reference is accepted for the pilot, described as a
  pseudonymous identifier, not anonymisation.
- **Identity** now uses the full SHA-256 (`withheld:<64 hex>`). Labels show 8 characters,
  lengthened automatically when two sources in a document would collide.
- **Documented:** a reference is stable only while the original path stays the same.
- **New tests:**
  - collisions of short references;
  - full-digest identity;
  - re-projecting a redacted payload changes nothing;
  - the library re-importing a shared artifact keeps its source IDs, and a local artifact
    of the same model gets the same IDs;
  - a moved file gets a new reference, and its manual link shows *Needs review* (all three
    backends).

### 2026-09-28 — B14 completed (optional R3)
- **Worker enrollment:** an admin enrolls a worker and gets its token once
  (`bidocwk_…`, hashed). The token reaches only `/api/v1/worker/*` and is revoked by
  deleting the worker.
  - A heartbeat every 30 s reports versions, input types and readiness; readiness lasts
    90 s.
  - `/capabilities` reports `processing` and `worker_status`.
- **Durable jobs** (`jobs.py`): the job row is authoritative, and claims are
  compare-and-set.
  - 120 s leases with renewal.
  - Expiry requeues the job up to 3 attempts; a manual retry allows 3 more.
  - Cancellation, fail with retryable or permanent errors, 24 h source retention, and
    `python -m bidoc_library cleanup` (run on start and by a daily Container Apps job).
- **Server-side finalization:** the worker stages a candidate, and `complete` publishes it
  as the requester.
  - The store re-checks the lease inside the publication commit and marks the job
    succeeded in the same commit: one SQLite transaction, or the Azure commit partition
    with the job row's ETag.
  - So a stale worker cannot publish (A39), a job publishes at most once (A16), and
    retries return the persisted outcome.
- **Generator worker mode:** `bidoc worker connect | run [--once] | disconnect`.
  - One job at a time, each in its own workspace.
  - A lease-renewal thread learns about cancellation and kills the extraction process
    tree (A17).
  - PBIX through the configured pbi-tools; PBIP project ZIPs extracted with safe-path and
    size checks, sibling folders kept.
- **Azure template:** `/api/v1/worker/*` is outside browser sign-in (token-only), and a
  daily cleanup job is added. The template check covers both.
- **Tests:**
  - `test_jobs.py`: 43 tests across LocalStore, in-memory Azure and Azurite;
  - `test_worker.py`: a real library server with the worker, including cancellation that
    kills the process tree; runs on Linux and Windows CI.
- **Deviation from the handoff:** worker routes are `/api/v1/worker/…`, not
  `/workers/{id}/…` and `/jobs/{id}/renew`, so one token-only prefix can bypass ingress
  sign-in.
- **Not verified:** real unattended PBIX extraction under the intended account (B16).

### 2026-09-28 — B14 merged; B15 completed
- Merged bi-doc-platform #15 at the owner's instruction. The Windows CI failure it hit (a
  ZIP test relied on backslash names, which Windows `zipfile` rewrites) was fixed before
  the merge: the check now also reads the raw entry name.
- **Process PBIX** (`#/processing`, publishers):
  - capability-gated: a clear unavailable notice with a generator link, and a disabled
    upload, until a worker is ready;
  - uploads with a progress bar, as a new document or a new version of an existing one;
  - a job list that refreshes every 2 s while work is active, with state, stage and
    attempt, the error, **Open document**, **Cancel** and **Retry**.
- **Settings** (`#/settings`): version, access mode, storage, health, search mode and
  worker status with last heartbeat. Administrators also get a worker list (readiness,
  inputs, versions, heartbeat, Revoke) and enrollment (token shown once).
- `/capabilities` adds `storage_backend`, `limits.source_bytes` and
  `worker_last_heartbeat_at`.
- **Browser acceptance:** Node acts as the worker. It covers gating, upload, live
  progress, the published result opening its document, failure with its error, retry,
  cancel, and settings with enrollment.

### 2026-09-28 — B15 merged; B16 prepared (owner-run)
- Merged bi-doc-platform #16 at the owner's instruction.
- **`docs/b16/CHECKLIST.md`:** a step-by-step owner run on the worker machine as the
  intended account.
  - Scenarios: signed-in baseline; after a reboot before sign-in; locked; signed out
    (only if unattended operation is claimed); the worker end to end, signed in and
    unattended; recovery after killing the worker mid-job; cancellation.
  - A table maps the verified conditions to what R3 may be released for.
- **`packaging/windows/b16/b16_gate.py`:**
  - records each run as one JSON line: real pbi-tools extraction, shared generation and
    validation, timings, counts, versions, account, and session facts (session id, window
    station, whether an input desktop exists, uptime);
  - creates the Task Scheduler tasks per scenario, and removes them;
  - runs one worker job;
  - writes the report. A condition counts as supported only with at least 2 passes and no
    failure in its latest 3 runs.
- `test_b16_gate.py` runs the script with the pbi-tools stand-in on Linux and Windows CI.
  On Windows it checks the session probes.

### 2026-09-29 — Live-connected and DirectQuery reports

- pbi-doc-gen now names the remote model behind a live-connected (thin) report, resolves DirectQuery-to-Analysis-Services tables to their real source, and shows the storage mode per source. The platform pins it at `20dfd59`.
- The Power BI adapter no longer assumes a PBIX has an embedded model (a thin PBIX used to fail), and a live report's remote model becomes a source object.
- Tested only on synthetic thin PBIX input here. Still to verify locally with pbi-tools: the DP-500 PBIX samples in pbi-doc-gen `pbix-samples/` (DirectQuery SQL Server, composite, Dual) through the platform.
- Not found publicly yet: a thin report on Azure/SQL Server Analysis Services; DirectQuery on Snowflake, Databricks, Oracle.

### 2026-09-29 — PBIX without pbi-tools (`bidoc generate --pbixray`)

- `bidoc generate --kind pbix --pbixray` extracts with pbixray (pbi-doc-gen `pbixray_extract`) instead of pbi-tools, in the same child process with the same timeout and cancellation handling. Any OS; install with `pip install "bi-doc-generator[pbixray]"`.
- It is an approximation of a pbi-tools extract, and this line described the first extractor only: the current portable reader does read shared expressions and parameters, roles and measure format strings, and report bookmarks come from the report parsers. Current coverage and limits are in `docs/portable-extraction.md`. pbi-tools stays the authoritative route.
- A real public PBIX (DP-500 lab 08: DirectQuery + Import, parameterised server) now goes end to end here: 6 tables, 2 measures, SQL Server source objects, personal path withheld, envelope validates. Tested in `apps/generator/tests/test_cli.py` (skipped when pbixray or the pbi-doc-gen samples are absent).
- Not covered: `bidoc batch`, the desktop app and the worker still use pbi-tools only. Not compared with a real pbi-tools extract of the same file.

### 2026-09-29 — Engines moved into this repository (`components/`)

- `components/power-bi` and `components/adf` are copies of `pbi-doc-gen@0b04cf4` and `adf-doc-gen@960b4bc`; the platform installs them locally instead of from pinned git URLs. Behaviour is unchanged: the engine code equals what was already pinned.
- Only deliberate differences: the CLI moved into the package (`pbidocgen/cli.py`, `adfdocgen/cli.py`, with a `generate_docs.py` shim), and `pbi-tools/` and most `pbix-samples/` were not imported (three small DP-500 files were, for `test_pbixray_extract.py`). `scripts/verify_components.py` checks the copies against the source repositories.
- CI runs the two component suites, and its skip guard now matches real unittest skip markers instead of the word "skipped" (an imported ADF test has that word in its name).
- The standalone repositories are unchanged for now; nothing here changes what they contain.

### 2026-09-29 — PBIR page ids

- A PBIR page is now identified by the `name` in its `page.json`, not its folder name. Reports whose folders are not named after the page (for example `ReportSection1`) no longer get a false "Declared page is missing from the extract" warning, and page order and the active page are matched correctly. Tests: `components/power-bi/tests/test_pbir_page_ids.py`.

### 2026-09-29 — Portable PBIX reader (opt-in)

- `bidoc generate --kind pbix --pbixray` and `pbi-doc-gen --pbix FILE --pbixray` extract a PBIX with the pinned portable reader (`pbidocgen/portable.py`) instead of pbi-tools. pbi-tools remains the default; batch, desktop and worker are unchanged. The earlier `pbixray_extract` module now delegates to the reader.
- pbixray is accepted only in the validated range `>=0.15.0,<0.16` (0.15.0–0.15.5 gave byte-identical documents on 121 public models). Contract tests keep the range, both `pyproject.toml` extras and the lock file consistent, and a weekly canary workflow (not a merge gate) checks the newest release. `bidoc doctor` names the installed version and the range.
- Untrusted-input limits: decompressed size is capped (larger of 256 MiB and 50x the input, at most 16 GiB and 90% of free disk; `BIDOC_MAX_DECOMPRESSED_BYTES`), the embedded metadata database is opened read-only with only plain tables accepted, and connection strings are redacted with an allowlist parser.
- Partial extractions (unsupported features found) make the document local-only and the shared profile still withholds query code.
- Not yet: ABF input and model pairing, the default-backend change, the Windows frozen build, and any same-file comparison with a real pbi-tools extract.

### 2026-09-29 — Tabular ABF input and explicit model pairing

- `bidoc generate --kind abf` documents an Analysis Services Tabular backup (`.abf`) with the portable reader (model-only; never needs pbi-tools). `.abf` files are also recognised by `bidoc batch` and the desktop file picker. `abf` is a new envelope input kind (schema and fixtures updated).
- `--model` pairs a thin report with an ABF, BIM or TMDL model the user names. The document states that server identity and backup freshness are not verified. A report that already has its own model is rejected rather than having it replaced.
- Checked on a real 7-table AdventureWorks Tabular backup, and on a thin PBIR report paired with it.
- Not covered: a matched real thin-report and backup pair (none found publicly), password-protected or multidimensional backups.

### 2026-09-29 — Default PBIX extractor: pbi-tools if it can run, otherwise portable

- `bidoc generate --kind pbix` now has `--backend auto|pbixray|pbi-tools` (default `auto`; `--pbixray` stays as an alias). Auto uses a configured pbi-tools; if none is configured it uses the portable reader; if a pbi-tools is configured but cannot run here (not Windows, no Power BI Desktop) it falls back to the portable reader and prints a note. An explicit `--pbi-tools` path that cannot run, or `--backend pbi-tools` with none available, is an error, never a silent fallback.
- Batch, the worker, the desktop app and `pbi-doc-gen --pbix` follow the same rule. A machine with pbi-tools configured behaves exactly as before.
- Behaviour change to be aware of: on a machine with no pbi-tools, PBIX generation now works (approximately) instead of stopping with "PBIX generation is unavailable". Portable output is labelled as such and suppresses deletion recommendations.
- Checked on the DP-500 composite PBIX on Linux: nothing configured (portable), a configured tool that cannot run (falls back with a note), `--backend pbi-tools` with none (error), an explicit unusable path (error).
### 2026-09-29 — Public-sample acceptance

- `python scripts/acceptance.py` fetches a checksum-pinned public corpus (`samples/manifest.json`: three DP-500 PBIX files, AdventureWorks Sales and Internet Sales PBIX, two live-connection PBIX fixtures, Microsoft's AdventureWorks Tabular backup and project, an Azure Data Factory template), generates local and shared documents for 12 cases (24 outputs), and checks each against an explicit question (storage modes, thin-report pages and visuals, the 19-measure backup, partial-coverage warnings, PBIR page order). `scripts/check_sample_html.cjs` opens all 24 in Chromium, switches every available tab and fails on any script error. `python scripts/test.py [--samples]` runs the seven suites, and the acceptance run with `--samples`.
- New CI job `portable-samples` runs both and uploads the documents as an artifact. Downloads are cached by the manifest's hash.
- The Tabular backup is genuine; its published model project is used to check measures, columns and roles field by field. The live-connection samples point at a different database than the backup, so the "thin report plus backup" case uses a synthetic report and is labelled as a pairing demonstration, not a discovered pair.
- Four files are fetched from the standalone `pbi-doc-gen` repository at a pinned commit (`20dfd59`): the three DP-500 files and AdventureWorks Sales. The rest come from Microsoft's `sql-server-samples` release, `Hugoberry/pbixray` and `Azure/Azure-DataFactory`. If `pbi-doc-gen` were deleted or made private those four downloads (and the job) would fail; archiving keeps it readable, so it is not a reason to avoid archiving.

### 2026-09-29 — Portable reader in the Windows frozen build

- The PyInstaller spec now ships pbixray with its metadata and modules, and `bidoc.exe` and the desktop executable accept `--portable-extract` (a frozen executable cannot run `python -m pbidocgen.portable`, so the extraction child process re-invokes the executable itself). Without this, the default from the previous change would have failed inside an installed app on a machine with no pbi-tools.
- Checked here: a PyInstaller build of the CLI from a clean, non-editable install (as `build.ps1` does), run with no Python on `PATH`, reports PBIX and ABF ready in `doctor` and generates both a PBIX and the AdventureWorks Tabular backup; the previous `-m` form is rejected by the frozen executable.
- `verify-install.ps1` gains checks that the bundled reader is present and in range, that PBIX is ready without pbi-tools, and that a PBIX generates with the installed executable (input: the 54 KB DP-500 composite fixture). They run in the `windows-installer` CI job on a hosted Windows runner.
- Still open: the same script on a clean Windows machine (the runner has Python on disk), and an ABF generation check on Windows.

### 2026-09-29 — Standalone engine repositories frozen

- `Ntmashaba/pbi-doc-gen` (last commit `0b04cf4`) and `Ntmashaba/adf-doc-gen` (last commit `960b4bc`) carry a README banner pointing here; their code and history are unchanged. Nothing had landed in either after the commits imported into `components/`, and neither had open pull requests.
- Not archived yet, and nothing requires a move first: archiving keeps a repository readable, so the four `pbi-doc-gen` downloads in `samples/manifest.json` (three DP-500 files and AdventureWorks Sales, at commit `20dfd59`) keep working. Deleting either repository, or making it private, would break them, so neither should be done. Repointing those downloads to files held in this repository would make the samples independent of the old repository; that is a maintenance improvement, not an archiving prerequisite.
- The `pbi-tools/` binaries and the larger `pbix-samples/` set exist only in `pbi-doc-gen`.

### 2026-09-30 — One backend policy for every entry point; capability wording

- Before: `bidoc generate` could fall back to the portable reader when a configured pbi-tools could not run, while batch and the desktop app called `diagnose()` directly and reported PBIX unavailable for the same configuration, and the worker advertised PBIX because a file existed at the configured path. Reproduced on the previous `main` with a configured pbi-tools that cannot run: `doctor`/batch/desktop said unavailable, `generate` and the worker used the portable reader; and a worker given an existing but unusable file advertised PBIX.
- Now `bidoc_generator/backend.py` (`select_backend`, `for_runner`) decides once, and `generate`, `batch`, the desktop app, the worker and `doctor` all use it. An explicit `--pbi-tools` / `--backend` is never overridden; a pbi-tools that was only configured or found on `PATH` falls back to the portable reader, with the reason printed, when it cannot run. ABF is always portable. The worker advertises PBIX only when the selected backend is present and can start. Extraction failures, including a program that cannot be launched, are structured errors; a failed extraction is never retried through the other backend.
- The reusable checks (file, Windows plus Power BI Desktop, a bounded 15 s launch probe) are in `pbidocgen/pbi_tools_runtime.py`, shared with the engine's own command line (`pbi-doc-gen --pbix`) without the engine importing the platform. The launch probe shows a file can be started; it is not a functional test.
- Tests: `apps/generator/tests/test_backend_consistency.py` (27, including the follow-up's process-tree and batch-reporting tests) observes doctor, batch, desktop, worker readiness and generate for the same machine state: nothing configured, usable tool, implicit tool that cannot run, existing but non-launchable executable (real launch), explicit invalid override, missing or unsupported pbixray, and ABF with an implicit pbi-tools. `test_resolve_tool.py` covers the engine's command line and the shared checks. Seven older tests that exercised the removed CLI helper were replaced by these.
- Wording: `--backend` help no longer claims "no roles or bookmarks / no shared M queries beyond parameters" (the reader does read roles, shared expressions and format strings; bookmarks come from the report parsers). `docs/portable-extraction.md` now separates thin-report detection, remote-model metadata (not retrieved), explicit pairing (snapshot only, identity and freshness unverified), DirectQuery lineage (depends on input and partition type) and the documented incomplete areas. The HTML's warning that field requirements cannot be resolved without a model is unchanged.
- Housekeeping wording corrected: archiving keeps a repository readable and is not blocked by the sample URLs; deleting a repository or making it private would break them. The manifest has four downloads from `pbi-doc-gen` (three DP-500 files and AdventureWorks Sales), not three. Repointing them is an independence improvement, not an archiving prerequisite. Nothing was archived, deleted or moved.
- Review follow-up (same day): the launch probe now runs in its own process group and ends the whole tree, bounded, on timeout and on interruption (previously only the immediate process was killed, leaving a hung executable's child running; reproduced by the reviewer and covered by a real parent-and-child test that fails with a parent-only kill). Batch runners keep the backend selection: a batch that uses the fallback records the reason once in its structured record, prints it once, and shows it once in the desktop batch view; covered by CLI (text and JSON) and desktop API tests.
- Fresh local results for this change (Linux, Python 3.11): `python scripts/test.py` passes all seven suites (power-bi 246, adf 35, contracts 34, engines 55, relationships 10, generator 76, library 182); `build_fixtures.py --check` passes; `scripts/acceptance.py` generates 24 outputs; `check_sample_html.cjs` opens all 24 in Chromium with no script errors; `check_desktop_browser.py` passes and the compiled `desktop.js` matches `desktop.ts`. CI results for this commit are on the pull request; earlier CI results in this file describe earlier commits.
- Not changed and not claimed: no same-file comparison with a real pbi-tools extract, no clean-machine Windows run, no genuine matched thin-report and ABF pair. Decompression limits, the pbixray range checks and canary, timeouts and cancellation, coverage and pairing warnings, disabled deletion recommendations for portable output, shared-output projection and the refusal to replace a local or composite model are untouched.
- Windows validation pack (prepared, not run): `docs/validation/windows-validation-pack.md` lists public inputs, exact commands, expected results and evidence for clean-machine install (V1), real pbi-tools execution (V2) and same-file comparison with the portable reader (V3); V1-V3 need the owner's Windows machine. New `scripts/compare_extractors.py` (unit-tested with synthetic extracts only; its pbi-tools side has never run) compares the two readers' model facts. The Windows installer job now also generates the public AdventureWorks ABF backup through `verify-install.ps1 -AbfArchive` (checksum-pinned in `samples/manifest.json`, MIT, 2.1 MB, downloaded and not committed). Still not claimed: parity, a clean-machine run, a real pbi-tools run, a matched thin-report/ABF pair (none public; the pairing demo stays synthetic and labelled).

### Next
- Owner: run `docs/b16/CHECKLIST.md` (and the earlier owner steps: W2, A13, A15,
  deployment, signing), then send back `VERIFICATION.md` and `results.jsonl`.
- Owner: run `packaging/windows/verify-install.ps1` on a clean Windows machine (the hosted runner has Python on disk); it now also covers the bundled portable reader and, with `-AbfArchive`, the public AdventureWorks ABF backup (checksum-pinned, MIT, 2.1 MB). The full owner checklist (clean install, real pbi-tools, same-file comparison with `scripts/compare_extractors.py`) is `docs/validation/windows-validation-pack.md`; none of it has been run yet.
- Portable extraction still to do: a same-file comparison against a real pbi-tools extract; a matched real thin-report and Tabular-backup pair (none is public); ABF in the hosted upload UI and the worker; dependency parity, so deletion recommendations can be enabled for portable output.
- Repository: archive `pbi-doc-gen` and `adf-doc-gen` when you choose to (archiving keeps them readable; do not delete them or make them private while `samples/manifest.json` points at `pbi-doc-gen`). Optionally repoint its four sample downloads into this repository for independence.
