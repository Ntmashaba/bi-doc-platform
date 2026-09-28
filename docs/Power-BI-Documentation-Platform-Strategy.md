# BI Documentation Platform — Power BI and ADF Strategy

Date: 28 September 2026. Version 1.2. Status: proposed implementation baseline, not an implemented or deployed product. Revised after adversarial review: publication safety, identity/scope, temporal relationships and recoverable commits.

## 1. Product direction

Build one independent team documentation library and one Windows desktop generator with separate Power BI and ADF engine adapters. The library accepts generated documents, organises and searches them, retains versions, and distributes the desktop installer. An optional registered Windows worker later allows users to submit PBIX processing jobs through the same website. The library must remain useful when no generator is connected.

Keep both existing Python analysis engines from pbi-doc-gen and adf-doc-gen; do not flatten their domain models into a single generic parser. This plan builds on the repository review supplied in the conversation; the 28 September adversarial review inspected pinned source snapshots and ran 163 Power BI and 26 ADF regression tests successfully. Additional synthetic probes exposed publication and identity defects. This is not Windows extraction or cloud validation.

## 2. User journeys and screens

| Journey | Intended behaviour |
|---|---|
| Browse documentation | Open the library, search titles or content, filter tags, open a report or an earlier version. |
| Publish existing documentation | Upload generated HTML or a complete document bundle; validate, identify report/version, index, then publish. |
| Generate locally | Download the Windows installer from the library, run prerequisite checks, select Power BI inputs or ADF repository folders, ARM exports, or resource JSON, generate and preview documentation. |
| Share local results | Export HTML/bundle for manual upload initially; add authenticated Publish to library in a later increment. |
| Process through the website | When a healthy compatible worker exists, upload PBIX, receive a job ID, track progress, and open published results. |
| Work offline | Generate and maintain a local library; export a portable index and search snapshot that opens without a server. |

Screens: Library with All documentation / Power BI / ADF entry points; Import documentation; specialised document viewer with Related documentation; Details and versions; Download generator; Processing queue; Settings and worker status. Without a worker, the processing screen explains the local generator route. Browsing and documentation import remain available.

## 3. Proposed technology selection

| Layer | Initial choice | Reason and boundary |
|---|---|---|
| Processing engine | Existing Power BI and ADF Python engines behind separate adapters and a common request/result API | Reuse parsing and rendering across CLI, desktop, and worker. |
| Hosted application | Python FastAPI serving a lightweight HTML/CSS/TypeScript interface | One deployable service; no separate frontend runtime required in production. |
| Desktop | pywebview with packaged Python through PyInstaller | Reuse web UI components in a Windows application; validate packaging on Windows. |
| Installer | Signed Windows installer around the packaged application; installer tooling selected during packaging spike | Include upgrade/uninstall behaviour; verify third-party redistribution terms rather than silently bundling prerequisites. |
| Azure hosting | Container Apps Consumption, initially minimum 0 and maximum 1 replica | Small pilot footprint; cold starts accepted. Start by testing 0.25 vCPU / 0.5 GiB and increase only from measurements. |
| Azure artifacts | Private Azure Blob Storage | Documents, versions, installer releases, and optional temporary PBIX inputs. Reuse suitable existing storage with separate containers and permissions. |
| Azure catalogue | Azure Table Storage, provisional pending commit/recovery spike | Small records with immutable Blob snapshots; adopt only after proving the atomic catalogue/event protocol and comparing engineering effort with an available relational backend. |
| Initial search | Bounded catalogue and per-document search data loaded into the browser | No dedicated search service for the pilot. Benchmark real volumes; introduce server indexing if payload size or latency becomes excessive. |
| Local persistence | Filesystem plus SQLite on a local persistent disk | Simple offline installation; do not put a shared SQLite database on Azure Blob Storage or treat container temporary disk as durable. |
| Job dispatch, later | Azure Queue Storage and an authenticated worker API | Worker claims jobs, reports progress, and publishes outputs. Local deployments can use a durable local job store. |
| Identity | Deployment adapter: local-only, trusted gateway, or Entra | No home-grown password database. Distinguish read and publish/delete permissions. |

These are design recommendations, not measured sizing guarantees. A client's existing supported hosting may be cheaper incrementally than introducing a new Azure service.

## 4. Document contract

Define a versioned manifest embedded as inert JSON in a self-contained HTML document; use a ZIP bundle when external assets are required. Include document type (power_bi or adf), envelope schema version, separately versioned native payload, stable document ID, unique revision ID, title, description, tags, generator version, generation time, content hash, searchable text, section anchors, and an asset inventory where applicable. Exclude local credentials and unnecessary machine paths.

The importer assigns trusted server-side storage paths and records who published when identity is available. It validates the manifest and assets without executing uploaded scripts. Known legacy artifacts require a validated native-schema adapter and regeneration into the safe publication profile. Unknown HTML or unsupported native schemas are rejected for shared publication until a safe conversion adapter exists; local file viewing remains separate.

Use immutable revision storage and conditional catalogue updates. Repeated uploads with identical full submitted-artifact bytes recover the original committed outcome. Imports are regenerated into safe stored artifacts; keep separate submitted and stored digests. The manifest-excluding content hash is not a deduplication key. Publish the current revision pointer only after safe artifacts and an immutable revision descriptor exist, atomically recording a committed publication event and a durable derived-state invalidation marker. Search and relationships may catch up after commit; their generation/status must be explicit. Keep failed uploads invisible and retryable. Concurrent updates must produce versions or a visible conflict, not silently overwrite each other.

Store searchable terms for measures, tables, sources, and prose where present. A search hit should open the relevant document section. A shared search index must contain only material accessible to its users; the pilot assumes one common team readership.

## 5. Windows processing capability

Expose a noninteractive CLI/worker mode in the packaged generator. The website submits a job to that worker rather than launching a desktop window. An administrator registers the worker and configures a fixed executable location. Do not execute arbitrary executables found on disk or accept executable paths from uploads.

If the web backend runs natively on the same Windows server, the worker may run beside it. If the website runs in a Linux container, a separately installed Windows worker polls for work through an authenticated connection. Installing an EXE on the Windows host does not make it executable inside a Linux container. A browser also cannot silently run an EXE on a user's machine.

Readiness requires tool/version checks, a successful representative extraction under the actual run account, writable working folders, resource checks, and worker heartbeats. Begin with one extraction at a time. Jobs need leases, timeouts, bounded retries, isolated workspaces, restart recovery, and idempotent publication. An offline worker leaves jobs visibly pending or failed with a retry option.

Microsoft lists Windows Server in Desktop requirements but recommends client Windows and explicitly states that Desktop does not support system accounts. This does not establish unattended extraction reliability. Validate the exact pbi-tools workflow, server version, account/session conditions, and restart behaviour before promising server support. Do not default the worker to LocalSystem. If unattended execution cannot pass the pilot, retain local generation and document the limitation. [1][2]

## 6. Cost strategy

The pilot assumes a single team, tens of users, hundreds rather than tens of thousands of documents, light daily publishing, and local PBIX extraction. These assumptions must be confirmed during deployment discovery.

Prefer existing client storage and registry where appropriate. Keep the cloud application focused on library work; avoid buying a Windows VM solely for extraction in the first release. Do not introduce AKS, an always-running database, a dedicated search service, or additional event infrastructure for the pilot without a demonstrated need.

Container Apps Consumption has monthly subscription-wide free grants of 180,000 vCPU-seconds, 360,000 GiB-seconds, and two million requests. Zero replicas incur no app resource-consumption charge. Other services, networking features, and storage can still cost money; grants may already be used elsewhere in the subscription. Cold starts are the tradeoff. [3]

Monthly budget must include compute beyond grants, stored document versions, storage operations, downloads/egress, container image registry, logs/retention, and any required private networking. Code signing and release maintenance also have costs. For optional central processing include Windows compute and operational support separately. Existing capacity reduces incremental spend, not necessarily total cost.

Do not quote a fixed rand amount until the Azure region, client agreement, existing resources, network requirements, volume, and traffic are known. Use the Azure calculator with those inputs, set budget alerts, limit logs, apply artifact/input retention, and measure a pilot billing period. Budget alerts are not automatic spending caps. [3][4]

## 7. Access and operational baseline

Local mode binds to loopback by default. Shared mode explicitly chooses network/gateway protection or Entra sign-in; Azure Container Apps supplies a built-in authentication option. Trust forwarded identity only from the configured protected gateway. Protect document downloads as well as the home page. [5]

Separate viewer and publisher permissions, even if a trusted pilot grants every member both. Render shared views using shipped, trusted viewer code and a validated sanitized payload; never execute uploaded scripts. Protect direct viewer URLs and desktop previews as well as embedded display. Validate archive paths and size limits. Scope worker permissions to processing, keep credentials out of artifacts, and avoid logging document contents.

Keep durable data outside the container image and temporary filesystem. Back up catalogue and artifacts, test restoration, and provide a way to rebuild search data. Raw PBIX files can contain business data; upload them only for requested server processing and use explicit retention/cleanup.

## 8. Phased delivery and acceptance

| Phase | Deliverable | Completion evidence |
|---|---|---|
| 0 — Validate foundations | Current code review; artifact contract; Windows extraction and desktop packaging spikes | Representative Power BI and ADF inputs generate readable artifacts; packaging works on a clean supported Windows machine with documented prerequisites. |
| 1 — Shared library | Mixed Power BI/ADF import, type filters, content search, revisions, specialised isolated viewers, contextual relationships, persistent storage adapters | Two team members can publish/browse both types; cross-links open the exact object in either direction; confidence and evidence are visible; duplicates/concurrent updates are handled; restart preserves data. |
| 2 — Desktop distribution | Windows UI, prerequisite diagnostics, batch queue, offline export, installer download page | No separately installed Python or Docker required; batch failures are visible; install/upgrade/uninstall tested; a complete offline snapshot works without network requests. |
| 3 — Azure pilot | Consumption deployment, private storage, chosen access mode, logs, retention, backup | Access boundaries tested; restart and recovery proven; cold-start/search timings measured; client-specific cost estimate and pilot bill reviewed. |
| 4 — Optional central processing | Registered Windows worker; website PBIX upload and job progress | Run-account extraction passes; interrupted jobs recover; worker outage is clear; retry cannot duplicate publication. |

The first meaningful product checkpoint is Phase 1: import a generated document, find it through shared search, update it, and retrieve both versions after restart. Phases 2 and 3 complete the initial client experience; Phase 4 does not block it.

## 9. Deferred features and deployment inputs

Defer automatic folder watching, arbitrary storage-drop discovery, multiple workers, per-document access rules, rich version diffs, a global dependency explorer, organisation-wide tenancy, and a dedicated search backend. Contextual cross-document links are part of the initial client release, not deferred with the explorer. Initially route uploads through the application for predictable indexing. If direct storage drops become necessary, add an idempotent ingestion process and reconciliation scan.

Collect before production deployment: Azure region; existing storage and registry; public-with-sign-in versus private network requirement; expected users/documents/sizes; retention; maximum monthly budget; installer approval/signing requirements; and available Windows worker environment. These inputs refine deployment and costing without blocking library development.

## Sources checked 28 September 2026

1. Microsoft, Power BI Desktop requirements and limitations: https://learn.microsoft.com/en-us/power-bi/fundamentals/desktop-get-the-desktop
2. pbi-tools feature matrix: https://pbi.tools/cli/feature-matrix.html
3. Microsoft, Container Apps billing: https://learn.microsoft.com/en-us/azure/container-apps/billing
4. Azure Table Storage pricing: https://azure.microsoft.com/en-us/pricing/details/storage/tables/
5. Microsoft, Container Apps authentication: https://learn.microsoft.com/en-us/azure/container-apps/authentication
6. PyInstaller documentation: https://pyinstaller.org/en/stable/
7. pywebview introduction: https://pywebview.flowrl.com/guide/
8. FastAPI documentation: https://fastapi.tiangolo.com/

The architecture, sequencing, initial sizing, and pilot assumptions are recommendations. Sources establish platform capabilities and constraints; they do not prove compatibility of this application's unbuilt integrations.


## 10. Shared library experience and relationship strategy (v1.1)

There remain two applications: a multi-engine generator and a shared library. The two existing engine repositories remain authoritative inputs; initially use adapters and pinned engine revisions/packages rather than an unreviewed repository merge. The future application repository may be a monorepo, but each engine remains independently testable and each application independently releasable.

Library navigation starts with All documentation, Power BI, and ADF. Global search spans both types and shows type badges. Filters include business area, environment, owner and tags. Power BI views retain models, measures and sources; ADF views retain pipelines, activities, triggers, data flows, issues and coverage. Use a common library shell around specialised documents.

Each viewer has a Related documentation panel. A Power BI source can link to the ADF pipeline/activity that writes its endpoint; an ADF activity can show consuming Power BI sources. Selecting a link opens the identified document revision and object, and Back restores the original selection and filters. Include breadcrumbs, source/target type, environment, relationship origin, confidence, evidence and version dates. No relationship result must say no upstream system exists; it means no match was found in the indexed supplied documents.

Detected links are derived from structured, redacted endpoint data, not filenames or HTML scraping. Full compatible endpoint identity and an explicit write can support an exact static match. Missing identity, dynamic expressions or opaque code produce possible matches. Contradictory known locations or environments reject automatic matching. A read or delete operation alone is not a producer. Exact means matching static evidence, not runtime execution or proof of freshness.

Manual links are added by publishers through document/object selectors and a reason. Label them Manual / user asserted; they never become confirmed technical evidence merely because somebody added them. Preserve author, timestamps, reason and change history. Maintain them separately from detected links so recomputation cannot erase them.

When a current revision changes, rebuild affected detected relationships using exact revision IDs and publish the resulting relationship generation atomically. Preserve older evidence for historical views. Show Updating or stale evidence explicitly while rebuilding. Re-resolve manual links using stable object IDs, never fuzzy names: missing targets become Needs review, and archived/unavailable targets remain identifiable without broken navigation. Viewing an older revision must not silently show relationships calculated against a newer source revision.

The existing ADF bridge is a starting point, not a verified relationship service. Source inspection found useful matching logic but also semantics requiring review: delete operations are grouped with writers and file paths are lowercased during matching. Preserve case-sensitive path distinctions and separate writes from deletions before offering exact producer claims. The inspection covered README.md, generate_docs.py, renderer.py and bridge.py; tests and container execution were not run.

ADF's JSON processing is a candidate for Linux-hosted generation. First ship local multi-engine generation and mixed document import. Add hosted ADF generation only after dependency/resource tests; advertise capabilities per engine/input type rather than a single Windows-ready flag. PBIX processing keeps its Windows prerequisite gate. Existing ADF secret redaction, factory/selection/partial coverage, dynamic/opaque flags and design-time-only claims are product requirements.

The first client release must demonstrate a mixed library and bidirectional contextual navigation. A global dependency graph is a later enhancement. Do not infer measure-level or column-level end-to-end lineage without supporting evidence. The version 1.2 decisions below apply to every release phase.

Additional repository references inspected: https://github.com/Ntmashaba/adf-doc-gen/blob/main/README.md ; https://github.com/Ntmashaba/adf-doc-gen/blob/main/generate_docs.py ; https://github.com/Ntmashaba/adf-doc-gen/blob/main/adfdocgen/renderer.py ; https://github.com/Ntmashaba/adf-doc-gen/blob/main/adfdocgen/bridge.py . These are mutable main-branch links; the implementation agent must pin and record its actual revisions.


## 11. Decisions adopted after adversarial review (v1.2)

These decisions refine the existing product direction. The handoff supplies the executable field, API and lifecycle requirements.

| Area | Decision |
|---|---|
| Safe publication | Each supported native schema has a versioned safe projection. Redact secrets and withhold literal/encoded entered-data bodies and uncertain raw code before rendering or indexing. Preserve useful structure and record omissions; do not promise byte-for-byte native preservation. Unsupported schemas cannot enter the shared library. |
| Publication identity | One document stream represents one logical asset, engine, environment and declared selection scope. Environment variants and selections have separate document IDs. Interrupted/incomplete extraction does not advance current. |
| Partial coverage | Coverage uncertainty is evidence, not proof of deletion. Only a complete snapshot of the same declared scope can establish object removal. |
| Endpoint evidence | Bind each operation to its physical endpoint and invocation context. Preserve SQL ports/instances, case distinctions and path encoding throughout the engine, not just in the bridge. |
| Stable objects | Version explicit engine-specific ID derivation; distinguish logical sources from repeated page usages. Unknown renames become new objects or require an audited explicit mapping. Never guess. |
| Publication consistency | Artifact presence is not publication. A conditional commit records the document pointer, publication event and catalogue sequence together. Retries recover that outcome; conflicted revisions never become current through reconciliation. |
| Search and relationships | Derived generations identify their catalogue snapshot. After a commit, show pending/updating or labelled prior results until the matching generation is ready. |
| Historical relationships | Pin relationship generation IDs in historical links. Document dates and evidence-computation dates are distinct. Manual assertions are versioned within those snapshots. |
| Viewer trust | Shared active views use trusted application renderers and sanitized native data. Uploaded executable content is not run. Preview has no desktop host bridge; published views are read-only for both engines. |
| Metadata | Audited catalogue overrides support title/description/owner/business-area/tags without changing historical artifacts. Environment belongs to stream identity; changing it creates a new stream. |
| Desktop publishing | A narrowly scoped publishing API accepts revocable tokens through an explicitly tested ingress exception. Browser-authenticated token issuance is outside that exception. |
| Offline export | Export a pinned local catalogue/relationship snapshot with relative object links and missing-target states. R2 does not synchronize hosted manual assertions back into the generator. |
| Worker | R3 stages results and uses server-side lease-checked finalization before publication. Checking a lease after a worker has published is insufficient. |

The initial supported publication profile must withhold raw M/SQL expressions by default unless a supported sanitizer can safely project them. Users see that code has been withheld and why; endpoint evidence is retained only where safe and sufficiently grounded. No source refresh or execution is introduced.

Run early spikes for safe projection, stable IDs, endpoint normalization, trusted viewer navigation, Azure commit recovery, realistic index sizes and Windows packaging. Keep manual relationships and mixed navigation within R1; keep the worker optional. A 10 MiB browser index across 500 documents allows roughly 20.5 KiB per document before overhead, so measure actual documents before committing to browser-only search.

Verified review baseline: pbi-doc-gen `a7d5565cc1d237f8efc4dc96e47b756d15c1b14a`; adf-doc-gen `7c8cfe501b9d1e79eb53478779056ef9f1c9d329`. Recheck current revisions when implementation starts. The review reproduced fake sensitive literals in Power BI output and ADF port/case collisions. These are required correctness changes, not permission to rewrite either engine.

Deliberate scope clarification: permissive unknown-HTML sharing is replaced by safe known-schema conversion. Hosted metadata curation is included; hosted document-body editing remains excluded. Changes resolve F01–F14 in BI-Documentation-Platform-Adversarial-Review.md. Specification resolution does not mean implementation or platform acceptance has passed.
