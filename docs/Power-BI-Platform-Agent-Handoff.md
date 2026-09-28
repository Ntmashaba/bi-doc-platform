# BI Documentation Platform: Power BI and ADF Agent Handoff

Version 1.2 · 28 September 2026 · Proposed build specification

## 1. Instructions to the implementing agent

Implement two independently runnable applications: a Windows multi-engine documentation generator and a shared Power BI/ADF documentation library. Inspect https://github.com/Ntmashaba/pbi-doc-gen and https://github.com/Ntmashaba/adf-doc-gen and reuse their Python analysis/rendering engines through separate adapters. Preserve native schema structure and useful analysis through the safe publication projections in section 17; preserve regression suites with reviewed corrections to unsafe or incorrect expectations. Read this entire specification before making changes. The accompanying strategy explains the rationale; this handoff governs implementation details. Record any conflict with actual repository capabilities before adjusting the design.

The adversarial review inspected pinned snapshots and ran 163 Power BI and 26 ADF tests successfully on Linux; synthetic probes exposed defects documented in section 17. This is not a complete code audit or Windows/cloud acceptance. The earlier review described main at a7d5565cc1d237f8efc4dc96e47b756d15c1b14a and 163 passing Python tests; these are historical observations, not a current baseline or permission to discard subsequent work. Inspect the current checkout, AGENTS.md instructions, branch, uncommitted changes, entry points, packaging, HTML assets, tests, and extraction integrations. Record the actual revision and baseline results. Preserve existing CLI behaviour and export formats unless a migration is explicitly documented.

Proceed through the backlog in order, producing a runnable increment at each gate. Routine implementation choices are delegated to you. Do not silently drop requirements, simulate successful extraction, mark unsupported features as complete, or use mock storage in the delivered runtime. Test fixtures are allowed in tests. Do not deploy resources, incur cloud charges, or publish releases without the applicable authorization. Local implementation and deployment templates are part of the work.

Do not rewrite either analysis engine, invent a new extraction method, introduce AI services, or require Azure for local execution. Do not spawn additional agents unless separately authorized. When infrastructure or Windows execution is unavailable, finish platform-independent work and identify the exact unverified gate; never imply that a Linux test proves a working Windows installer.

## 2. Product boundaries and delivery scope

**Application A — Generator:** Power BI and ADF adapters, desktop UI, CLI, local history, batch generation, prerequisite diagnostics, document export, portable library export, and publishing to Application B. A later worker mode accepts hosted jobs using the same processing engine.

**Application B — Library:** mixed-type document import, Power BI/ADF category navigation, contextual detected/manual relationships, catalogue, content search, isolated viewing, immutable revisions, archive/restore, installer distribution, storage adapters, and deployment-dependent access. Later it can accept source uploads and dispatch processing jobs.

The library must start, import both artifact types, and serve documents without Power BI Desktop, pbi-tools, the generator application, or a Windows worker installed.

Delivery gates:

1. R1: local/container library, artifact contract, both generator CLI adapters, mixed-library generation/import/search/versioning, and detected/manual contextual navigation.
2. R2: Windows desktop application and installer, portable offline export, Azure storage/deployment profile, and direct publish.
3. R3: optional registered Windows processing worker, source uploads, job recovery, and capability-aware UI.

R1 and R2 constitute the initial client product. R3 is explicitly optional and cannot block their release. Multi-tenancy, document-specific permissions, automated storage-drop discovery, automatic folder watching, semantic/AI search, document editing, rich version diffs, a global dependency explorer, and multiple simultaneous extraction workers are out of scope.

## 3. Repository and technology decisions

Use one repository with independently built applications. Proposed structure (adapt names if the existing package layout makes migration unnecessary):

| Path | Responsibility |
|---|---|
| packages/engines/power_bi and packages/engines/adf | Adapters around pinned existing engines; retain native payloads and tests |
| packages/relationships | Versioned endpoint matching and evidence rules shared by hosted/offline workflows |
| packages/contracts | Manifest schema, validators, golden fixtures, request/result types |
| apps/library | FastAPI service, static frontend, catalogue, access and storage adapters |
| apps/generator | pywebview desktop shell, CLI, batch manager, later worker mode |
| ui | Shared CSS/components where useful; separate app entry points |
| tests/contract | Cross-application fixtures and compatibility tests |
| tests/e2e | Real end-to-end publication, persistence, and search tests |
| deploy | Dockerfile, local compose example, Azure infrastructure and runbook |
| packaging/windows | PyInstaller configuration and Windows installer definition |
| docs | Architecture decisions, setup, deployment, troubleshooting, release notes |

Keep both engine import paths compatible initially. Do not combine or move their repositories without assessing history, licensing, tests and release impact; pin engine package/revision dependencies first. Use pyproject.toml packages and locked dependencies; select a supported Python version compatible with the existing engine and Windows dependencies during the baseline gate. Record that exact version. Use FastAPI/Pydantic for API validation; HTML/CSS/TypeScript with a build step for the library frontend; no Node runtime in the production container. Use pywebview/PyInstaller for desktop packaging. Select and document the installer tool after a short Windows packaging test; its output must support installation, upgrade, and uninstall. Do not treat a raw PyInstaller binary as a completed installer.

Local persistence: SQLite on a local persistent filesystem, artifacts in managed folders. Azure: Blob Storage for artifacts and Azure Table Storage for catalogue/job records, using managed identity where deployed. No shared SQLite file on network/object storage. SQLite is supported for one application instance; Azure storage must use conditional writes even when the pilot has one replica.

## 4. UX specification

Use a consistent accessible interface: labelled controls, visible keyboard focus, status text alongside colour, responsive tables, clear empty states. Retain existing generated-document functionality where compatible with the isolated viewer. Put generation and upload progress outside the generated report itself.

### Library screens

| Screen | Required actions and states |
|---|---|
| Library home | All documentation / Power BI / ADF entry points; global search; type, business area, environment, owner and tag filters; active/archived selector for publishers; title, description, tags, latest publication time and version per report; Import documentation and Download generator actions. Empty state explains how to add the first document. |
| Import | Select HTML/ZIP; show detected identity/title/version; catalogue metadata overrides previewed separately from immutable artifact metadata; show duplicate, validation failure, new report, or new version before completion. Never request executable paths. |
| Report details | Title, description, tags, source generation date, library publication date, current version, revision history, Open and Download; publishers can archive/restore with confirmation. |
| Viewer | Specialised isolated Power BI/ADF display within a common shell, document title, selected revision, breadcrumbs and Related documentation panel; back restores origin/filter/selection. Section and relationship links open exact revision/object anchors. Rendering errors offer download and diagnostics. |
| Downloads | Approved Windows installer version, release date, checksum, prerequisites and release notes. If none exists, display an honest unavailable state rather than a dead link. |
| Settings | Selected storage/access mode and health status; no secrets. R3 adds worker readiness and last heartbeat. |
| Processing (R3) | Upload PBIX or project ZIP, progress states, errors and retry; unavailable explanation and local generator link when no compatible worker is ready. |

Search behaviour: case-insensitive AND matching of whitespace-separated words, with title matches ranked first, then tags, then section heading/body. Match terms as substrings for v1; escape all rendered snippets. Return report title, section heading, a short text snippet and a stable revision-specific section link. Only current active revisions are searched by default. A blank query displays the catalogue. Tag filtering uses exact tag matches and combines with search. Older revisions remain viewable but are excluded from default search. Clearly distinguish no documents from no matching results.

### Generator screens

| Screen | Required actions and states |
|---|---|
| Start/history | Choose Power BI or ADF; add PBIX files, select complete PBIP project folder or ADF Git/ARM/resource JSON inputs, reopen previous outputs, choose local library location. |
| Prerequisites | Detected versions and paths; readiness per input type; clear fix instructions. PBIP and ADF generation stay enabled when only PBIX prerequisites are missing. |
| Batch review | Discovered inputs, stable report identities, output selection and title defaults; do not treat a lone PBIP pointer as a complete project. |
| Processing | Per-item queued/validating/extracting/analysing/rendering/completed/failed/cancelled states; elapsed time and warnings; UI stays responsive. Progress percentages only when measurable. |
| Result | Open documentation, export bundle, add to local library, publish, retry failed item. Successful items survive other batch failures. |
| Library connection | Library URL and scoped publishing credential entry; connection check; clear expired/revoked credential state. Never store credentials in generated artifacts or logs. |

Closing with active work prompts to wait or cancel. R2 desktop processing is not a background service: an interrupted active job is marked interrupted/failed on next launch and is retryable. Do not claim it continued after the app exited. Offline generation, viewing, and portable export must work with network disconnected.

## 5. Artifact contract v1

Self-contained HTML is the canonical interchange format for R1. Embed exactly one inert script element with id `pbidoc-manifest`, type `application/json`. The importer parses it without executing HTML. Escape `<` as `\u003c` within serialized JSON to prevent a closing script sequence in user content. Cap the manifest at 16 MiB UTF-8 for the initial release, configurable. Native payload inclusion is required; do not silently drop it to fit. Preserve the 25 MiB HTML cap unless administrator configuration changes it.

Required fields and exact semantics:

| Field | Type and validation |
|---|---|
| schema_version | Integer, exactly 1; publication envelope version, independent of native engine schemas |
| document_type | Enum power_bi or adf; immutable for a document_id |
| classification | Object: business_area, environment, owner; each string 0–200 characters, empty means unknown |
| native_payload | Object: schema_version (string or integer), data (object), generator (string); retain safe projected engine payload; raw sensitive/uncertain fields are omitted with a projection report |
| objects | Array of stable object descriptors and endpoints defined in section 16 |
| document_id | Lowercase UUID string; stable across revisions and renames |
| revision_id | Lowercase UUID string; unique for a generated revision |
| title | Trimmed string, 1–200 characters |
| description | String, 0–2000 characters |
| tags | Array of 0–20 distinct trimmed strings, each 1–50 characters |
| generated_at | RFC 3339 UTC timestamp ending Z |
| generator | Object with name and version strings, each 1–100 characters |
| source | Object with kind enum pbix, pbip, tmdl, bim, pbir, extracted, adf_git, adf_arm, adf_resources, legacy; label string 1–255; optional sha256 of the source input |
| publication | Object: asset_id, environment_key, scope_key, scope_descriptor, snapshot_state=complete; immutable stream tuple for a document_id |
| projection | Object: policy_version, native_schema, omissions, coverage_warnings; required safe-publication evidence |
| navigation | Validated object/section-to-view registry; only shipped adapter view IDs and typed arguments |
| identity_version | Nonempty version of the engine object-ID mapping |
| content_sha256 | 64 lowercase hexadecimal characters, calculated using the rule below |
| sections | Array of 0–10000 objects: id (unique anchor matching `[A-Za-z][A-Za-z0-9_-]{0,127}`), title (1–200), text (0–100000 characters) |

All fields except source.sha256 are required, including publication, projection, navigation and identity_version. Classification.environment is a display label for the immutable environment_key, not an independent matching override. Reject unknown top-level fields for v1; schema changes require a new supported version. Cap all section text at 4 MiB UTF-8 combined. Do not embed absolute paths, credentials or source data rows. Sections describe documentation content, not source data rows. Classification, native_payload and objects are required additions before schema v1 is released. If an earlier schema v1 has already shipped, use a new version and an explicit migration instead of redefining it.

Hash rule to avoid a self-reference: take the exact final UTF-8 HTML bytes, replace the complete manifest element (opening tag through closing tag) with the ASCII bytes `<!--PBIDOC-MANIFEST-->`, then SHA-256 that result. Preserve all other bytes, including line endings. Reject zero or multiple manifest elements. Producer and consumer use the same shared implementation and byte-level golden fixtures. Store a separate server-calculated SHA-256 of the entire uploaded artifact for exact duplicate detection. Search text and metadata are validated but are not cryptographically authenticated by this hash. Native ADF schemaVersion (currently described as 2 in its README) is not the publication schema_version; preserve it independently.

The agent must commit a JSON Schema, a valid generated HTML fixture with a real computed hash, fixtures containing escaped script-like text, and invalid fixtures for unsupported version, duplicate manifest, hash mismatch, and missing anchor. Do not hand-write a fake checksum in a nominally valid sample.

Document identity: section 17 defines stream scope and object continuity. IDs are global across both types; reject a type change on an existing ID with 409. First generation assigns a UUID and stores it in a sidecar adjacent to the source if writable, otherwise in the generator's local mapping. Provide a visible 'use existing report identity' option for relocated/copied inputs. New inputs from different machines are not assumed identical based on filename. Reusing an ID is an explicit action. Revision IDs change on regeneration; duplicate full artifact bytes do not create another revision.

ZIP extension, required in R2: one root `document.html`, optional files only under `assets/`; manifest stays in document.html. Paths must be relative POSIX paths; reject traversal, symlinks, absolute paths, duplicate/case-colliding entries and encrypted archives. No nested archives. Include an `assets` array in the ZIP profile manifest with path and SHA-256 per file; this is the one explicitly allowed v1 extension. The HTML hash covers the document and the listed asset hashes must each verify. Self-contained HTML must omit assets. No external network assets in offline exports.

Legacy HTML import: only known supported native schemas can be converted to the safe publication profile. Extract native data without evaluating scripts, assign/confirm stream identity, project safely, and regenerate using a trusted renderer. Preview the conversion and omissions. Do not publish or retain unsafe original bytes. Unknown HTML or unsupported native schema returns 422 UNSUPPORTED_SAFE_PROJECTION; local file opening is a separate capability, not shared publication. Converted artifacts use normal revision/deduplication rules.

Initial limits, configurable by administrator: HTML 25 MiB; ZIP 100 MiB compressed / 250 MiB expanded / 2000 entries; reject oversized requests with 413 and explain the limit. These are pilot policy defaults, not measured platform limits.

## 6. Catalogue, persistence and publication

Entities:

| Entity | Fields |
|---|---|
| Document | document_id, document_type, classification, title, description, tags, current_revision_id, archived, created_at, updated_at, etag |
| Revision | document_id, revision_id, schema_version, generated_at, published_at, publisher_subject (nullable), artifact_key, artifact_sha256, content_sha256, size_bytes, section_count, status |
| Import | import_id, document_id/revision_id when known, state, error_code/message, created_at, completed_at, idempotency_key |
| Release | version, platform=windows-x64, artifact_key, sha256, release_notes, published_at, approved |
| Publish token | token_id, subject, token_hash, scopes, expires_at, revoked_at; never plaintext secret |
| Job/worker (R3) | Defined in section 10 |

Local artifact path: `artifacts/{document_id}/{revision_id}/document.html` and optional assets. Azure blob keys use the same layout. Temporary imports go under `staging/{import_id}/`. Never build filesystem paths from titles or client filenames. Catalogue schema changes use versioned, repeatable migrations with backup guidance.

Azure Table layout: document catalogue rows under partition `documents`, row key document UUID; revisions under partition `revisions:{document_id}`, row key revision UUID; imports under `imports`, releases under `releases`. This intentionally simple single-team layout needs load testing before scale-out. Use ETags for document current-pointer updates. Immutable artifact creation uses create-if-absent semantics.

Publication steps: validate request → stage artifact → validate manifest/assets → calculate checksums → resolve identity/revision → persist safe immutable artifact and prepared revision → atomically commit document pointer, publication event and catalogue sequence → mark import complete with derived-state status. Document pointer is authoritative. A crash before pointer update leaves a prepared revision; reconciliation may complete only using its original concurrency precondition, otherwise marks conflict; a crash after pointer update must not hide the published artifact. Readers never follow staging keys. Cross-blob/table operations are not a transaction; implement the stated recovery behaviour explicitly.

Re-importing identical bytes recovers the original committed publication outcome; prepared/conflicted bytes never imply successful publication. Same revision ID with different bytes is a 409 conflict. Concurrent different revisions for one document require the supplied expected document ETag; the losing update returns 409 and can be retried after review, never silently last-write-wins. Archive excludes documents from normal catalogue/search, retains history, and is reversible. Permanent purge is outside v1 UI; use an administrator retention operation with a dry-run list.

Search data is derived only from committed publication events and their referenced safe manifests. For the pilot, return a permission-filtered index for current active revisions and search in-browser. At 10 MiB uncompressed index or failed performance gates, add server-side search using the same matching semantics before expanding rollout. Cache by catalogue generation/ETag; updates must invalidate results. Browser caches must be cleared on logout. Azure implementation may rebuild an immutable index snapshot and atomically change its pointer; no in-memory-only source of truth.

## 7. HTTP API v1

Prefix `/api/v1`. UUID identifiers, UTC timestamps, JSON responses except binary downloads and multipart uploads. Pagination uses opaque `cursor`, `limit` default 50/max 200. Error body: `{ "error": { "code": "REVISION_CONFLICT", "message": "Readable explanation", "request_id": "UUID", "details": {} } }`. Do not expose paths, stack traces or credentials. Commit generated OpenAPI and integration fixtures.

| Method/path | Request | Success / principal errors |
|---|---|---|
| GET /health/live | None | 200 process live |
| GET /health/ready | None | 200 ready / 503 storage or required configuration unavailable; no secrets |
| GET /capabilities | None | 200 supported manifest versions, limits, access mode, can_publish, processing[{engine,input_types,available,reason}], worker status, installer availability |
| GET /documents | q optional title filter, document_type, business_area, environment, owner, tag, archived=false, cursor, limit | 200 `{items,next_cursor}` |
| GET /documents/{id} | None | 200 Document and ETag header / 404 |
| GET /documents/{id}/revisions | cursor, limit | 200 `{items,next_cursor}` / 404 |
| POST /imports | multipart file; optional legacy_metadata JSON; optional target_document_id; Idempotency-Key header required; If-Match required when updating existing document | 201 `{import_id,document_id,revision_id,status:"completed",duplicate:false,catalogue_sequence:123,indexing_state:"pending"}`; 200 duplicate:true; 400 malformed, 409 conflict, 413 limit, 422 invalid contract |
| GET /imports/{id} | None | 200 import status / 404 |
| GET /documents/{id}/revisions/{rev}/download | None | 200 admitted safe HTML or ZIP with attachment disposition / 404 |
| GET /documents/{id}/revisions/{rev}/view | None | 200 isolated viewer content / 404 |
| GET /search-index | If-None-Match optional | 200 `{generation,documents:[{document_id,revision_id,document_type,classification,title,tags,sections}]}` with ETag, or 304 |
| POST /documents/{id}/archive | If-Match required | 200 updated document / 409 stale |
| POST /documents/{id}/restore | If-Match required | 200 updated document / 409 stale |
| GET /releases/latest?platform=windows-x64 | None | 200 `{version,sha256,release_notes,download_url}` / 404 no approved release |
| GET /releases/{version}/download | None | 200 approved installer attachment / 404 |
| POST /publish-tokens (R2) | authenticated publisher; `{label,expires_in_days:1..30}` | 201 `{token_id,token,expires_at}` shown once |
| DELETE /publish-tokens/{id} (R2) | token owner or admin | 204 revoked / 404 |

All protected endpoints return 401 for missing/invalid identity and 403 for insufficient role. Health probes reveal no document content. Import is synchronous within configured bounds for R1; respond only after durable publication, returning catalogue_sequence and indexing_state=ready|pending|failed. A completed import does not imply indexing has finished; the UI presents that distinction. If actual imports exceed the measured request budget, introduce durable queued import execution with 202 and GET status before release; never rely on an untracked in-process background task. Failed connections retry using the same idempotency key; retry recovers the import ID and durable outcome even if the initial response was lost. Store idempotency outcomes for seven days; same key with different payload returns 409.

## 8. Desktop publishing, access and HTML isolation

Access modes: `local` binds loopback with a local owner policy; `gateway` requires trusted identity from the configured protected ingress; `entra` uses deployment authentication and maps claims to viewer/publisher/admin. No automatic unauthenticated fallback if a configured identity provider fails. A shared trusted-network deployment may explicitly choose a common team principal and network perimeter; label the lack of individual attribution in its runbook.

R2 direct publishing uses a revocable 256-bit scoped token stored hashed server-side and protected by Windows credential storage on desktop. The dedicated /api/v1/publishing namespace and authentication matrix in section 17 govern every request. Browser token issuance/revocation remains outside the ingress exception. Expiry is 1–30 days; revoked credentials or subjects that lose publisher permission cannot publish. Manual export/import remains available.

Shared viewers execute only shipped, versioned renderer code against the supported safe payload, never uploaded JavaScript. Use a sandboxed iframe without allow-same-origin, forms, popups or top navigation and enforce response-level CSP sandbox/restrictions on direct viewer URLs too. Serve only controlled assets; a separate viewer origin remains available if required, with scoped asset authorization and no app cookies. Unknown native schemas are rejected for shared publication. Both native viewers run read-only in published mode. Desktop previews are isolated from all pywebview host APIs. Escape catalogue metadata and snippets. Download only admitted safe artifacts as attachments. Section 17 defines local API protection and portable viewer rules.

Portable export contains index.html, report artifacts and bundled catalogue/search JavaScript (not fetch of local JSON files). Use relative links and local assets. Rebuild the snapshot on additions; a static export does not silently watch folders or persist changes. Test via file:// in Edge on Windows with networking disabled.

## 9. Engine and desktop execution interfaces

Create a pure orchestration entry point `generate(request, progress, cancellation) -> result`, independent of HTTP, UI and Azure. Request fields: engine (power_bi or adf), source path, source kind, document_id, output directory, requested export formats, metadata overrides, and explicit full-model/report-only policy. Result: status, document/revision IDs, artifact paths, warnings, structured errors, timings and tool versions. Source/executable paths stay local configuration; they are never untrusted HTTP request parameters.

Provide CLI commands under a chosen stable executable name: `generate`, `doctor`, `export-library`, `publish`, and later `worker`. Preserve the old CLI through a wrapper. `doctor --json` reports capabilities and actionable missing prerequisites. CLI output can be human-readable or structured JSON; exit codes: 0 success, 2 invalid input/config, 3 prerequisites missing, 4 generation failure, 5 partial batch failure, 130 cancelled. Document exact flags in generated help and examples.

Run extraction in a child process with a fixed configured tool path, argument arrays (no shell interpolation), timeout and process-tree cancellation. Begin with one concurrent PBIX extraction. Separate each item's workspace. Do not delete a previous successful output until replacement publication is complete. Clean temporary files after completion or on restart with a bounded retention policy. Do not refresh source data as part of documentation generation.

## 10. Optional worker protocol (R3)

Worker is a mode of the generator, not a third product. It can run beside a native Windows-hosted library or connect outbound to a Linux-hosted library. Browser and Linux container code cannot directly execute a Windows host EXE. Administrator configures and enrolls the executable; do not scan and execute arbitrary disk matches.

Worker record: worker_id, supported_input_types, engine_version, extractor_version, readiness, last_heartbeat_at, enrolled_subject. Job record: job_id, source_key/hash, requested_by, state, attempt, max_attempts=3, worker_id, lease_token_hash, lease_expires_at, created_at, started_at, completed_at, error, output document/revision IDs, cancellation_requested.

States: queued → leased → running → publishing → succeeded; queued → cancelled; leased/running → cancel_requested → cancelled; leased/running/publishing → failed. Retryable failures or expired leases requeue while attempt < 3; otherwise failed. Cancellation during publication cannot roll back a revision already committed; return succeeded with a clear too-late cancellation outcome. Status progress stages may include extraction, analysis and rendering without changing durable job states.

Heartbeat every 30 seconds; readiness expires after 90 seconds. Job lease 120 seconds, renewed every 30 seconds. Claim is a conditional atomic update; stale workers cannot complete jobs without the current lease token. Publication is performed only by server finalization after checking the current lease; workers stage candidates and cannot call generic publishing endpoints. Stable job identity and persisted committed outcomes make retries idempotent. Queue messages are hints; the durable job row is authoritative. Only one PBIX job is processed at a time per initial worker.

| API addition | Contract |
|---|---|
| POST /jobs | multipart source, input_type, document_id optional; 202 `{job_id,state:"queued"}`; reject unready capabilities with 409 WORKER_UNAVAILABLE |
| GET /jobs/{id} | 200 job state/progress/result or 404 |
| POST /jobs/{id}/cancel | 202 cancellation requested, or 409 terminal state |
| POST /jobs/{id}/retry | 202 new attempt on eligible failed job, or 409 |
| POST /workers/{id}/heartbeat | authenticated enrolled worker; versions/capabilities; 204 |
| POST /workers/{id}/claim | 200 `{job,lease_token,lease_expires_at,input_download_url}` or 204 no job |
| POST /jobs/{id}/renew | lease token; 200 new expiry and cancellation flag / 409 stale |
| POST /jobs/{id}/progress | lease token and stage; 204 / 409 stale |
| POST /jobs/{id}/complete | lease token, attempt ID and staged result ID; server finalizes publication; 200 committed outcome / 409 stale |
| POST /jobs/{id}/fail | lease token, error code, retryable flag; 200 resulting state / 409 stale |

Source downloads require scoped worker authorization; short-lived URLs must not appear in logs. Source upload default cap 1 GiB, configurable; use streaming to storage. Worker source ZIP extraction follows safe path/size rules; expected PBIP sibling folders must be preserved inside its isolated root. Retain raw sources for 24 hours after terminal state by default, administrator configurable; retain failed-job diagnostics without raw content. Register a daily cleanup path appropriate to the deployment; do not depend on traffic to eventually delete sensitive inputs.

Server readiness gate: demonstrate representative PBIX extraction under the actual intended account after reboot, with session locked and with session logged off if unattended operation is claimed. Record supported conditions honestly. Microsoft documents restrictions on Desktop/system accounts; no default LocalSystem promise. Missing infrastructure means this gate remains explicitly unverified, and the local generator is the supported route.

## 11. Deployment and build requirements

Local library: one documented install/start command using Python and one Docker command mounting a persistent data directory; no Docker Compose dependency for the single-container mode. Bind loopback by default. Production container runs non-root, includes built frontend assets, health checks and pinned runtime dependencies; excludes extraction tools. All catalogue/artifacts persist outside its writable ephemeral layer.

Configuration must be typed and validated: DATA_BACKEND=local|azure; LOCAL_DATA_DIR; AZURE_STORAGE_ACCOUNT_URL; blob container/table names; AUTH_MODE; trusted gateway configuration; VIEWER_ORIGIN when used; upload limits; retention; supported schema versions; log level. Secrets come from environment/credential facilities, not repository files. Provide a checked-in example with placeholders only.

Azure templates: Container Apps Consumption with minimum replicas 0, maximum 1 for the pilot; measured starting allocation 0.25 vCPU/0.5 GiB; managed identity and scoped storage permissions; optional Entra configuration; existing storage/registry parameters; diagnostics retention and cost alerts. No new AKS cluster, database server, paid search tier, or Windows VM by default. Include cold-start behaviour and client-specific costing worksheet. Budget alerts are notifications, not guaranteed spending caps.

Windows builds run on Windows CI or a documented Windows build host. Installer includes our Python runtime but checks externally installed prerequisites. Review licensing before bundling pbi-tools or other vendor software. Produce checksum, version metadata and release notes. Development installers may be unsigned and must be clearly labelled; production signing requires the client's signing setup and is a release gate. Upgrades preserve configuration and local library data; uninstall does not delete user documents without a separate explicit choice.

## 12. Ordered implementation backlog

| ID | Depends on | Deliverable and completion gate |
|---|---|---|
| B01 | None | Inspect both repositories; record baselines and representative sizes; early safe-projection/identity/viewer and Windows packaging feasibility spikes. Unavailable Windows remains explicitly unverified. |
| B02 | B01 | Freeze section 17 decisions, engine ID mappings and operation/endpoint bindings; multi-tool publication envelope and native payload schema mappings, validators, real valid/invalid fixtures and hash algorithm tests. |
| B03 | B02 | Engine orchestration API and CLI adapter; produce a valid self-contained artifact from real supported Power BI and ADF sources while both old CLI suites pass with documented corrections to wrong assertions; fix early endpoint collisions and add safe publication projections. |
| B04 | B02 | Local catalogue/artifact repositories, migrations, immutable revisions and crash-safe publication. |
| B05 | B04 | Library import/read/archive APIs, OpenAPI, upload limits and idempotency. |
| B06 | B05 | Mixed-library UX, isolated specialised viewers, content search, type filters and contextual relationship API/UI from section 16. |
| B07 | B03,B06 | Mixed Power BI/ADF R1 end-to-end and relationship gate and durable Docker volume smoke test; deliver runnable library. |
| B08 | B03 | Desktop shell, prerequisite diagnostics, batch queue, cancellation and local history. |
| B09 | B08 | Complete Windows installer and clean-machine test after the B01 feasibility spike; explicit verification report. |
| B10 | B07 | Confirm provisional Azure Table choice through section 17 commit/recovery proof and backend cost comparison; Azure storage adapters with the same contract suite; conditional writes and recovery tested. |
| B11 | B09,B10 | Installer download, publishing credentials and direct publish; manual workflow also retained. |
| B12 | B08 | ZIP asset support and portable export; file:// offline acceptance. |
| B13 | B10,B11,B12 | Azure templates, deployment/access runbook, backups/restore and R2 release checklist. |
| B14 | R2 | Worker enrollment/protocol and durable job lifecycle. |
| B15 | B14 | PBIX/project upload UI, capability gating, progress and retries. |
| B16 | B15 | Real Windows unattended extraction/recovery gate; release R3 only for verified configurations. |

Do not estimate completion dates without inspecting the repository and available build environments. At each gate report changed behaviour, relevant tests run, runnable commands, artifacts, and remaining unverified platform conditions.

## 13. Acceptance tests

| ID | Scenario and expected result |
|---|---|
| A01 | Generate a real supported PBIP fixture → import → search for a measure → open its section. Report identity and content match. |
| A02 | Change the source, regenerate using the same document ID, import with current ETag. Both revisions remain readable; search defaults to the new revision. |
| A03 | Restart/recreate the library container with the same volume. Catalogue, versions and search remain available. |
| A04 | Repeat identical import and retry with identical idempotency key. One revision exists; response identifies duplicate. Different bytes for the same revision/key fail with 409. |
| A05 | Two publishers update the same report using the same ETag. One succeeds; the other receives a conflict; neither silently overwrites the other. |
| A06 | Interrupt publication at artifact write, revision save, and pointer update. Recovery produces no catalogue entry pointing to absent content and no duplicate current publication. |
| A07 | Missing extraction prerequisites: PBIX generation disabled with diagnosis; PBIP generation and library browsing still work. |
| A08 | Three-item batch with one invalid input. Two succeed and remain readable; failed item can be retried. |
| A09 | Malicious script attempts to read parent cookies, navigate top window, or contact an external host. Viewer isolation blocks it; legitimate report interactions are exercised. |
| A10 | Archive traversal, duplicate paths, oversized ZIP and invalid hash/schema are rejected without outside writes or published remnants. |
| A11 | Viewer cannot import/archive; expired or revoked publishing token cannot upload; direct download obeys the same readership access policy. |
| A12 | Offline export opens via file:// in Windows Edge with networking disabled, including search and document links. |
| A13 | Installer on clean supported Windows without Python/Docker: setup, prerequisite diagnosis, generation, upgrade and uninstall behave as documented. |
| A14 | Back up and restore catalogue/artifacts into a clean deployment. IDs, revisions and checksums match. |
| A15 | Azure adapter runs publication/concurrency tests against real disposable Azure resources when authorized. Emulator tests alone are not represented as live Azure validation. |
| A16 | Worker unavailable: browsing/import work; processing displays unavailable. During a job, kill worker and recover lease: at most one final revision is published. |
| A17 | Worker cancellation/timeout kills extraction process tree and cleans workspace; another job can run. |
| A18 | Optional Windows server passes the exact session/account scenarios claimed in its support matrix. Otherwise mark that mode unsupported/unverified. |

Pilot performance targets (acceptance targets, not promises): catalogue usable within 2 seconds after a warm API request; in-browser search results within 300 ms after typing stops for a representative 500-document index under the 10 MiB bound; benchmark on a documented ordinary client machine/network. Measure cold-start latency separately. Include at least 10 representative large documents and report actual index size and memory use. If targets fail, tune or implement server search; do not weaken results silently.

## 14. Required final handoff from the implementing agent

Provide runnable source, locked dependencies, schema/OpenAPI, fixture artifacts, tests, build scripts, local/container instructions, Windows packaging instructions, deployment templates, configuration reference, backup/recovery guidance, compatibility matrix and release notes. Include actual test output summaries and explicit missing Windows/Azure validation. Do not claim a working EXE unless it was built and exercised on Windows. Do not claim hosted deployment unless one was actually performed and verified.

Client inputs still needed before production: Azure region and resource ownership; approved access/network model; expected document volumes; retention; budget ceiling; installer signing/approval; and optional Windows host/account. Use configurable placeholders and continue local development while these are unresolved.

## 15. Copyable kickoff instruction

> Implement the BI Documentation Platform for Power BI and ADF using this handoff and the accompanying strategy. Treat the generator and library as independently runnable applications in one repository. Begin with B01 and proceed through R1 before desktop/Azure extensions. Preserve both existing engines and CLI behaviour, commit executable contracts and meaningful tests, and produce a runnable end-to-end increment. Continue through R2 where the environment permits; identify platform validation gates honestly. R3 is a separately gated optional worker capability. Do not deploy billable resources or publish releases without authorization. Record concrete deviations and evidence instead of silently changing scope.

Reference strategy: Power-BI-Documentation-Platform-Strategy.md. Platform research and source links are in that strategy; this document specifies proposed application behaviour rather than claiming the integrations already exist.


## 16. Multi-tool catalogue and relationship contract (required v1.1 scope)

This section adds required detail to R1. It is not the deferred global dependency explorer. The common catalogue is technology-neutral; native engines and viewers remain specialised. An initial Windows desktop app contains both adapters. ADF must not inherit PBIX-only readiness checks. Linux-hosted ADF generation is a later separately advertised capability after runtime testing; mixed document ingestion requires no extraction engine at all.

### Structured objects

Each envelope objects entry has: object_id (stable adapter ID, max 512 characters), kind (source/table/measure/pipeline/activity/trigger/dataflow/dataset/linked_service/file/other), label (1–512), section_id (declared adapter target), bindings (array defined in section 17), dynamic (boolean), opaque (boolean), and coverage (complete/selection/partial/unknown). Do not attach an operations array to one ambiguous object endpoint. For activities also allow parent_object_id referencing the pipeline; IDs are scoped to document_id and stable across revisions when the same logical object persists. Use existing stable engine keys where possible; never use display labels alone.

Endpoint fields are system, server, instance, port, database, schema, object, storage_account, container, path, url; values are string or null; port is an integer 1–65535 or null. Preserve originals plus a separately computed normalized comparison representation and normalization version. Do not lowercase case-sensitive storage paths, decode encoded separators into a different resource, or assume SQL object case equivalence where collation is unknown. Retain engine evidence and emit possible rather than exact when identity semantics cannot be established.

Classification environment separates Production/Test/Development; normalize known aliases with explicit configuration. Different known environments block automatic matching. Missing environment must be visible; it cannot override contradictory physical endpoints. Owner/business-area are filters, not identity evidence. No required connection to live Azure or Power BI services.

### Detected relationship rules

Relationships join an ADF producer endpoint and Power BI source endpoint, then offer navigation in both directions. Source/target object IDs and document/revision IDs are mandatory; store evidence endpoint values, contributing activity/pipeline, rule version, and coverage/dynamic/opaque flags. ADF parent pipeline is a navigation target, not evidence that every activity in it writes the same object.

Use confidence `exact_static` or `possible`, with origin `detected`. Exact requires complete compatible physical identity, compatible environment, explicit write operation and no uncertainty affecting the asserted link. Explicit reads may appear as related readers but never as producers; deletes get a distinct `deletes` relationship, never `produces`. Dynamic/opaque or missing location downgrades or prevents a claim. Conflicting known locations reject a match. Views/procedures do not imply hidden base-table lineage. No-match wording: 'No relationship found in the indexed documents.' Partial inputs retain a coverage warning.

Before reusing adfdocgen/bridge.py, correct and test its treatment of deletes as writers and blanket path lowercasing. Review folder-prefix matches: overlap can imply a possible folder/file association, not automatically an exact file producer. Existing bridge UI verdicts are not assumed correct simply because they exist. Preserve original CLI behaviour where feasible, but document deliberate correctness fixes and add regression tests. Audit common.py physical_key/_norm_host and upstream aggregation too; bridge-only fixes cannot recover merged endpoints.

### Manual relationships

Publisher chooses source document/object and target document/object, relationship kind (`related_to`, `produces`, `consumes`, `deletes`) and a mandatory reason (1–2000 characters). Object selection is optional for document-level related_to only. Store origin=manual, confidence=user_asserted, creator subject, creation/update timestamps, stable document/object references and revision IDs at assertion time. Display 'Manual — user asserted' and reason; never relabel manual claims as exact static matches.

Editing/deleting a manual link requires publisher permission, ETag and an audit entry. Detected links cannot be edited through the manual endpoint. A manual link can coexist with a detected one but the UI groups them and presents both evidence sources. Manual user assertions do not modify the engine payload or erase uncertainty.

### Revision lifecycle and persistence

Persist DetectedRelationship fields: relationship_id, source_document_id, source_revision_id, source_object_id, target_document_id, target_revision_id, target_object_id, kind, confidence, evidence, rule_version, generation_id. Persist ManualRelationship fields from above plus ETag, status=active|needs_review|archived_target, and audit history. Persist RelationshipGeneration: id, input revision vector/hash, state=building|ready|failed, timestamps and error.

Relationship direction is stored once; retrieve incoming and outgoing links. On publication/archive/restore, identify affected endpoint candidates and rebuild a durable generation. For the bounded R1 pilot, computation may occur synchronously after document commit under a serialized catalogue-generation update; the catalogue commit atomically advances its durable dirty sequence first. On failure/restart, retry from that marker; never rely on an untracked background task. For larger collections add a durable queue. Commit the complete new generation pointer conditionally; stale computations cannot overwrite a newer catalogue generation.

Keep exact revision references in detected evidence. Current views use the latest ready generation only when its input revision vector matches; otherwise display Updating relationships with visibly labelled prior evidence. Historical views pin an explicit relationship generation under section 17, including counterpart revision/date and assertion versions; never silently redirect to latest. Retain relationship snapshots for retained document revisions.

Manual links re-resolve against stable IDs as current revisions change. If an object disappears from a complete snapshot of the same declared scope, mark Needs review; omission outside scope or incomplete extraction is not deletion; do not guess by similar name. Retain assertion-time evidence. Archived targets are excluded from ordinary detected results and shown as unavailable/archived in retained manual/history views. Apply access checks to relationship endpoints and evidence; do not leak titles or metadata of inaccessible documents.

Local schema includes relationship_generations, detected_relationships, manual_relationships and relationship_audit tables. Azure stores immutable detected-generation data separately, while authoritative manual assertion/audit commits and catalogue sequence use the common commit partition from section 17; the initial bounded implementation may maintain per-document relationship indexes to avoid whole-table queries. Relationship data and audit are included in backup/restore.

### API additions

All paths below use /api/v1; existing permission and error rules apply.

| Route | Contract |
|---|---|
| GET /documents/{id}/objects?revision_id=...&cursor=... | 200 paginated object descriptors and section links |
| GET /documents/{id}/relationships?revision_id=...&generation_id=...&object_id=...&cursor=... | 200 generation state, input revisions, incoming/outgoing records with origin/confidence/evidence, next_cursor |
| POST /relationships/manual | Publisher JSON: source_document_id, source_revision_id, source_object_id nullable, target_document_id, target_revision_id, target_object_id nullable, expected_catalogue_sequence, kind, reason; 201 record and ETag; 422 missing/nonexistent objects |
| PATCH /relationships/manual/{id} | Publisher, If-Match; kind/reason/target changes; 200 record + audit; 409 stale |
| DELETE /relationships/manual/{id} | Publisher, If-Match; 204 soft-delete + audit; 409 stale |

GET capabilities adds can_manage_relationships and relationship_schema_version. Search results include document_type and classification; type-specific terms such as ADF pipeline/activity names and Power BI measure names remain searchable. Backend and frontend filtering semantics must match.

### Navigation and viewer integration

Library shell navigation: All documentation, Power BI, ADF; persistent filters for business area/environment/owner/tags. Search spans both types unless filtered. Use document titles rather than generic 'report' labels throughout shared UI. Empty type view offers appropriate import/generator guidance.

Related documentation panel groups upstream producers, downstream consumers, other readers/deletions, and manual links. Each row displays type, document/object, environment, version date, origin/confidence and expandable evidence. Selection opens `/library/documents/{document_id}/revisions/{revision_id}?object={object_id}&generation={generation_id}`. The shell resolves the object to a validated anchor, records origin navigation state, and loads the isolated viewer. Back restores scroll, object and library filters. A missing target shows Needs review or Archived, not a blank iframe.

For sandboxed viewers, use a versioned postMessage protocol: `{protocol:'bi-doc-viewer',version:1,type:'select-object',object_id:'...'}` and shell-to-viewer `{protocol:'bi-doc-viewer',version:1,type:'navigate-object',object_id:'...',section_id:'...'}`. Validate event.source against the active iframe, exact message shape and IDs against the active revision; sandbox origin may be opaque so do not trust origin='null' alone. Messages never contain HTML, credentials, executable paths or arbitrary URLs. Supported adapters reveal hidden tabs/panels before scrolling to the anchor. Legacy documents without messaging still open to their supported section or provide a clearly labelled document-level fallback.

Native Power BI and ADF details editing/download functionality must not invalidate the publication contract: a new exported HTML must regenerate its manifest/hash and revision ID, or the hosted viewer disables that editing action and directs users to the generator. Preserve published revisions as immutable. Treat native payload schema migration independently from envelope schema migration.

### Additional acceptance gates

| ID | Expected result |
|---|---|
| A19 | Import one ADF and one Power BI artifact; All shows both, type filters separate them, owner/environment/business-area filters and global content search behave consistently. |
| A20 | Exact static endpoint and explicit ADF write produce a contextual link; follow Power BI source → ADF activity/pipeline → Back with selection restored. Reverse navigation identifies the consumer. |
| A21 | Same name on different servers/environments is not joined; missing location is possible at most; dynamic/opaque paths never show exact; delete-only activity never shows producer. |
| A22 | Case-distinct blob paths remain distinct; folder-prefix overlap alone is not exact file production. |
| A23 | New revision rebuilds affected links; historical view retains original counterpart revision evidence; stale generation is visibly updating and cannot overwrite a newer one. |
| A24 | Publisher adds manual link/reason, viewer cannot; regeneration preserves it; disappearing target object marks Needs review without fuzzy reassignment. |
| A25 | Native payload, ADF redaction and factory/selection/partial flags survive publication; unsupported native schema is rejected for shared publication with UNSUPPORTED_SAFE_PROJECTION; no unsafe original retained or fabricated objects. |
| A26 | Both engine CLI baseline suites pass; no Power BI prerequisites are needed for local ADF generation or mixed HTML import. |
| A27 | Iframe object-selection messages cannot inject HTML, navigate arbitrary URLs or access another document; both native viewers open the intended hidden tab/section. |
| A28 | Backup/restore retains detected generations, manual assertions and audit; no inaccessible counterpart metadata leaks through relationship results. |

Extend B02 with envelope/native mappings and endpoint fixtures; B03 with both adapters; B04/B05 with relationship persistence/API; B06 with mixed navigation and relationship panels; B07 must pass A19–A25 and A27 alongside core R1 tests. B08/B09 include ADF desktop inputs and per-engine diagnostics. B13 includes A28. Global graph visualisation remains deferred; do not defer contextual links under that exclusion.

The reviewed ADF files were README.md, generate_docs.py, adfdocgen/renderer.py and adfdocgen/bridge.py on main. This was source inspection, not execution or a complete audit. B01 must pin both current revisions, inspect native schema/anchor behaviour and run the actual suites before implementing the mappings above.


## 17. Normative publication and lifecycle decisions (v1.2)

This section supplies the cross-feature invariants used by B02–B16. Version 1.2 is a specification version, not a claim that an envelope has shipped. Add these fields before the first envelope release; if any v1 envelope was distributed, increment its schema and provide migration rather than changing the meaning of v1 silently.

### 17.1 Safe publication and renderer boundary — F01/F05/F10

Define a versioned safe projection for each supported native schema. The default shared profile withholds raw M/SQL code, embedded row literals, compressed/encoded entered-data bodies, credentials, secret-bearing URLs and unnecessary machine paths. A supported sanitizer may retain a safe code projection only with explicit tests and omission markers; arbitrary strings are not certified safe by keyword scanning alone. Retain safe endpoint identity and explain reduced evidence. The importer re-applies the supported projection and validates metadata; it never trusts a producer-supplied safety label. Projection precedes rendering, envelope creation, search, CSV/JSON exports and downloads. Never preserve an unsafe copy in another embedded DATA block.

Projection reports list field paths, omission reasons and coverage effects without echoing sensitive values. Both engines require seeded secret/data fixtures. Unknown native schemas and unstructured HTML are rejected from shared publication; known legacy input is converted and regenerated. Converted source originals are not retained. Safe native schema preservation means structural/semantic compatibility with explicit omissions, not byte preservation.

The library viewer uses its own trusted renderer version, selected from a server-controlled registry. Uploaded script bytes never become executable viewer code. A validated target registry maps object/section IDs to known view IDs and arguments; the importer validates that registry without executing HTML. Browser tests prove target rendering. Protocol messages include revision ID and a per-load channel ID; a viewer-ready handshake precedes navigation. Validate the active iframe, channel, revision and object IDs. Published views disable edited-HTML actions in both engines; local generator editing creates a fresh projected artifact/revision.

Direct hosted views enforce the same response-level sandbox and CSP as embedded views. No arbitrary navigation is dispatched from payload values. Desktop preview never receives the host bridge. Loopback APIs validate Host and Origin and require a per-session secret for mutations; browser-authenticated mutations require CSRF protection. Test unauthorized origins and direct viewer navigation. Portable views use generated trusted code with sanitized data, not copies of arbitrary imported scripts. Downloads remain safe artifacts, but opening a file independently is outside hosted session enforcement.

### 17.2 Stream and object identity — F02/F03/F04

`publication` contains `asset_id` (UUID for the logical source asset), `environment_key` (nonempty normalized key; unknown is explicit), `scope_key` (stable identifier), `scope_descriptor` (engine-specific canonical selection), and `snapshot_state` (complete). The immutable stream tuple is asset_id/document_type/environment_key/scope_key. One document_id identifies one such tuple. Reject tuple changes with 409 STREAM_IDENTITY_CONFLICT. Classification.environment is its display label. Moving from unknown to a named environment creates a new stream with an audited related_to association; no automatic reassignment.

Full-factory and selected-pipeline exports use different scope keys/document IDs. A successful complete extraction of a declared selection is publishable even when its external dependency coverage is partial; an interrupted or failed extraction is not. Incomplete attempts remain local diagnostics and cannot advance current. Only absence inside a complete snapshot of the identical declared scope establishes removal. Do not infer deletion from unresolved/omitted evidence.

Source sidecars map stream tuples to IDs. Copy/import UI explicitly chooses a new asset/stream or an existing identity. No filename inference. Each adapter commits an identity_version algorithm and rename/copy/delete/re-create fixtures. Prefer durable native identifiers where available; otherwise persist explicit sidecar object mappings and require an audited user mapping for a rename without durable native identity. Unknown rename means new object and Needs review, never fuzzy reassignment. PBI source IDs are independent of page usages; activity identity includes pipeline/nested context. Normalization changes include an explicit migration and manual-link review report.

Each `binding` has binding_id, operation (read/write/delete/execute), endpoint, invocation_context, evidence_refs, resolution (static/partial/dynamic/opaque/unsupported), coverage and normalization_version. Evidence references identify safe native fields; no secret code is copied. Bind read A and write B separately even within one activity. Parameterized dataset bindings are evaluated in the supplied invocation context without executing expressions or connecting to services; unresolved values remain uncertain.

Endpoint comparison preserves raw host, instance, port, database, schema, object and encoded path. Unknown SQL collation does not justify case folding. Connector-specific rules establish equivalence; unknown port defaults or aliases cannot erase contradictions. Test distinctions before native entity aggregation as well as in matching. Existing incorrect bridge expectations may be corrected with documented evidence; engine rewrites remain out of scope.

### 17.3 Commit, recovery and derived state — F06/F11

All imported artifacts are regenerated into safe canonical stored artifacts; executable uploaded originals are not distributed. Record submitted_artifact_sha256 for request byte-duplicate detection and stored_artifact_sha256 for integrity of regenerated bytes. Validate the incoming manifest hash before conversion; recompute the stored manifest/hash afterwards. Retain the submitted digest but not unsafe original content. Deduplication consults persisted conversion/commit outcomes rather than rerendering on each retry. A revision descriptor is immutable and may exist in prepared state before publication. Import records additionally persist authenticated subject, artifact digest, stream tuple, expected document ETag, state, committed_event_id, catalogue_sequence and original outcome. States are validating/prepared/committed/conflicted/failed. Ready artifact bytes are not proof of committed publication.

For the Azure pilot, put document rows, the global catalogue-state row and immutable publication-event rows in the same `documents` partition. A conditional transaction updates the document pointer, increments catalogue_sequence and writes the event with revision descriptor/digest references. The sequence itself is the durable dirty marker: derived pointers below that sequence require rebuild. Prepared revision descriptors and Blob artifacts are written first; their creation is not commit. SQLite performs the equivalent commit in one transaction. Do not require cross-partition atomicity.

The original expected ETag is retained through crashes. Reconciliation first checks whether its publication event committed; if so return the recorded outcome. Otherwise retry using the original precondition or mark conflict, never promote over an intervening commit. Committed history is enumerated from events, not every stored revision. Same revision ID/different bytes is 409. Byte duplicates recover the original committed result without changing current; prepared/conflicted duplicates return that state/conflict. Idempotency keys are scoped to principal and operation and bind target/preconditions plus artifact digest. Reusing a key with changed intent is 409.

`content_sha256` covers only the HTML outside the manifest and is not a semantic digest. The full submitted-artifact digest drives request byte duplicates; the full stored-artifact digest verifies safe published bytes. Search/relationship cache identity uses catalogue sequence plus rule/projection versions, not content_sha256.

A committed import returns indexing_state and catalogue_sequence. Current catalogue/detail reads use authoritative committed rows. Derived search and relationship responses identify their sequence and state. During rebuild the UI labels prior results stale and offers current catalogue browsing; do not present stale hits as current truth. Rebuild immutable snapshots, then conditionally switch their pointers only if inputs still match. Startup and request entry points reconcile durable dirty sequences; a deployment scheduler provides bounded maintenance/cleanup when traffic is absent. An in-memory lock is not the Azure concurrency mechanism.

Metadata overrides and manual assertions/audit records use conditional commits in the same partition and advance the sequence. Store large projected payloads, evidence collections and derived snapshots in Blob objects with small pointer rows. B10 must prove this layout under crash/concurrency tests and record costs before accepting Table Storage. If it fails, record an ADR and choose an available transactional backend without weakening the contracts.

Backup records a committed catalogue sequence, versioned document/metadata/assertion state, generation pointers and all referenced immutable blobs. Restore validates references/checksums and rebuilds derived snapshots if necessary. Do not prune referenced revisions/generations during retention.

### 17.4 Historical evidence and manual assertions — F07

Every relationship generation records the catalogue sequence, exact revision vector, rule version and assertion versions. Responses and shareable viewer links carry generation_id in addition to revision_id. Without generation_id, a historical request selects the latest retained ready generation containing that revision, reports its computation date and redirects/updates navigation to the pinned generation URL. If none exists, show Evidence unavailable; do not substitute current evidence. The default is explicitly latest known evidence for that revision, not evidence known at original publication.

Pinned generations never change when counterpart revisions or manual assertions change. For current requests whose vector no longer matches, show Updating with explicitly labelled prior generation. Manual POST/PATCH includes the selected source/target revision IDs and expected catalogue sequence; validate atomically and return 409 SELECTION_STALE if context changed. Assertions retain author, reason, revisions, audit and tombstones. Disappearance rules follow 17.2.

### 17.5 Metadata and desktop authentication — F08/F12

Add `PATCH /documents/{id}/metadata`, publisher-only with If-Match, accepting title, description, tags, business_area and owner overrides, plus a required reason. Null removes an override; empty values follow field validation. Return effective metadata, immutable revision metadata, override provenance and new ETag/sequence. Do not modify native artifacts. Environment and scope are immutable stream fields. Snapshot overrides with catalogue generations so historical views remain reproducible.

| Route group | Authentication | Scope |
|---|---|---|
| /api/v1/publish-tokens, revoke | Browser Entra/gateway or explicit local-owner session; CSRF protection | Issue/revoke own tokens; admin revocation audited |
| /api/v1/publishing/capabilities | Application publish token | Limits and safe schema capabilities only |
| /api/v1/publishing/documents/{id} | Application publish token plus current publisher authorization | Publication metadata/ETag for an explicitly requested document |
| /api/v1/publishing/imports and /imports/{id} | Application publish token | Publish and inspect own import outcomes; same validation/commit service as browser imports |
| /api/v1/publishing/results/{import_id} | Application publish token | Safe result readback for own committed import |
| Other /api/v1 routes | Selected browser identity policy | Existing viewer/publisher/admin permissions |

Only /api/v1/publishing/* bypasses ingress browser authentication; every route there fails closed at application token validation. It cannot issue tokens, archive, edit metadata or manage relationships. No redirects forward credentials to another host. Require HTTPS outside loopback. Revocation and publisher-role removal are checked on requests; deployments unable to determine current authorization must document/enforce an equivalent centrally revocable subject policy before direct publish is enabled. Prove the whole cookie-free sequence through actual ingress in R2; templates alone leave that gate unverified.

### 17.6 Offline and worker boundaries — F09/F13

R2 portable export is a local snapshot: selected safe artifacts, effective local catalogue metadata, exact relationship generation, local manual assertions if present, target registry, relative URL map and bundled search data. Hosted manual assertions are not automatically synchronized into local history; hosted snapshot download/sync is deferred. Cross-links outside the export show Not included. Export pins generation and revisions and makes no API calls. Test file:// in Edge offline, including object→object→Back. Export metadata records the projection/rule versions and snapshot date.

R3 workers have staging/progress rights only. Add `POST /jobs/{id}/results` to stage an immutable candidate under job/attempt identity with lease validation. `/complete` accepts staged_result_id and attempt_id, rechecks current lease at the commit boundary and performs server-side publication. For Azure, keep the authoritative job/lease row in the commit partition so finalization conditionally updates it with document pointer/event/sequence. Lease renewals/reassignment modify its ETag. Thus a stale worker cannot commit between lease validation and publication. SQLite uses one transaction. Retries return the persisted committed output even if a new attempt generates different bytes. Cancellation before commit prevents publication; cancellation after commit reports the committed success. This gate applies only to R3.

### 17.7 Additional acceptance gates and evidence — F01–F14

| ID | Required result |
|---|---|
| A29 | Seeded secrets and entered-data markers absent from every published representation; omissions visible; unsupported projection rejected. |
| A30 | Different SQL ports and unproven case equivalence remain distinct throughout native aggregation and matching. |
| A31 | Copy A→B and parameterized dataset invocations bind each operation to the correct endpoint; page additions do not change PBI source IDs. |
| A32 | Selected ADF exports cannot replace a full-scope stream; incomplete attempts cannot imply deletion or advance current. |
| A33 | P1/A1→P1/A2→P2/A2 plus manual edits leaves pinned generation URLs reproducible; stale assertion selectors conflict. |
| A34 | Crash, intervening commit and duplicate retry cannot produce false completion, uncommitted history or stale promotion. |
| A35 | Embedded/direct/local preview paths use trusted code and safe data; unauthorized origins and host-bridge access fail. |
| A36 | Cookie-free desktop publish/ETag/retry/readback works through configured ingress; token escalation/revocation cases fail. |
| A37 | Both native viewers remain read-only when published; permitted local edits produce a new valid projected revision. |
| A38 | Portable snapshot supports bidirectional relative object links and Back; omitted targets are explicit; zero API/network requests. |
| A39 | A stale worker resuming after reassignment cannot finalize; current worker commits once despite differing candidate bytes. |
| A40 | Metadata overlays preserve artifact bytes and historical provenance; stream identity cannot be edited through metadata API. |
| A41 | Representative large documents measure import, memory, search and relationship rebuild costs; fallback server search preserves full matching semantics. |

B07 includes A29–A35, A37 and A40. B10 repeats recovery/consistency gates on the Azure adapter; live tests remain authorization/environment gated. B13 includes A36, A38, A41 and complete backup restoration. B16 includes A39. Early B01 packaging feasibility and later B09 installer acceptance are separate gates.

Review baseline: Power BI a7d5565cc1d237f8efc4dc96e47b756d15c1b14a (163 tests); ADF 7c8cfe501b9d1e79eb53478779056ef9f1c9d329 (26 tests). These passed on Linux before these changes were implemented. The fake-sensitive-value and endpoint-collision probes failed the new intended guarantees. No new guarantee in this specification is marked implemented merely because its requirement is written down.
