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
| B07–B16 | Not started | |
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

### Next
- Merge engine PRs #4; re-pin the engines to their merge commits.
- Then B07: R1 end-to-end gate (A19–A25, A27, A29–A35, A37, A40), including legacy import and metadata overrides.
- Earlier note, now done: engine changes (surface `lineageTag`; keep SQL port/case in ADF `physical_key`; Delete as its own operation; escape `<` in `DATA`);
  shared projection with the *Include query code* option; adapters emitting envelope v1.
