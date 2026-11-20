# Search and relationships (B06a)

## Derived state

- **What is derived:** search snapshots and relationship generations, built only from committed documents (`apps/library/bidoc_library/derived.py`).
- **When:** a rebuild runs synchronously after every committed change (import, archive, restore, manual link) and again on any read that finds it behind. There is no untracked background task.
- **Stale work never wins:** a rebuild records the catalogue sequence it read and switches its pointer only if that sequence is still current.
- **Imports** report `indexing_state: ready`, or `failed` when the rebuild failed; the publication itself stays committed either way.

Measured with 124 real documents (29 pre-extracted Microsoft Power BI reports and the 95 Azure-DataFactory templates, `apps/library/tests/check_library_scale.py`):

| Measure | Result |
|---|---|
| Search index | 580 KB (≈ 4.7 KB per document); 500 documents ≈ 2.3 MB, inside the 10 MiB browser budget |
| Full rebuild (search + relationships) | 1.1 s |
| Reference search | 2.8 ms per query |
| Publish | median 19 ms, max 0.9 s (including re-projection and re-rendering) |

## Search semantics v1

Reference: `bidoc_library/search.py`. The browser implementation (B06b) must match it.
- **Matching:** case-insensitive AND of words, as substrings.
- **Ranking:** title, then tags, then sections.
- **Result target:** each result points to the section with the most terms in its heading, then the most in heading plus text.
- **Filters** are exact.
- **Blank query** returns the catalogue.
- Only current, active revisions are indexed.
- Query code withheld from shared documents is not in the index at all.

## Detected relationships `rel-rules/2`

Package `packages/relationships` (pure, standard library only; the library and the offline export use the same code).
Sources are **Data Factory activities** and **SQL Agent job steps**; targets are **Power BI sources**.

| Situation | Result |
|---|---|
| ADF or SQL Agent write; SQL server, port, instance, database, schema and object all known and equal as written; both statically resolved | `produces`, `exact_static` |
| ADF file write to the same account, container and path | `produces`, `exact_static` |
| SQL Agent step that processes or refreshes an Analysis Services model, and a Power BI source that reads that model: same server and instance, same database **named by name** (TMSL), both statically resolved (a live-connected report) | `produces`, `exact_static` |
| The same, but the job names the database by its XMLA `DatabaseID` | `produces`, `possible` (an ID is usually, not always, the model's name) |
| Location part missing on one side (a SQL Agent T-SQL step has no server: it runs on the job's own); letter-case-only difference; folder containing the file; dynamic, opaque or partial resolution (a Power BI `AnalysisServices.Database` import query names no table, so it is partial) | `possible` |
| ADF delete / ADF or SQL Agent read of the same endpoint | `deletes` / `reads`, never `produces` |
| SQL Agent backup, restore, alter, delete, sync or clear-cache of a model; executing a stored procedure | no relationship (only processing and refresh load a model's data; a procedure's effect is not known statically) |
| An Analysis Services endpoint against a SQL table or a file, either way round | no relationship (a model is never a table) |
| Different known server, port, instance, database, account, container or environment | no relationship |
| Environment undeclared on either side | allowed, with the note "environment not declared on both documents" |

- **Stored once:** each relationship is stored with source = ADF activity or SQL Agent job step (and its parent pipeline
  or job) and target = Power BI source, and is served in both directions. The library groups both producers under
  "Upstream: pipelines and jobs that write or refresh this".
- **Evidence:** raw endpoints, both resolutions, environments, binding IDs and notes. A SQL Agent processing link says
  what the job runs, for example "the job's process (ProcessFull) targets Sales in this model".
- **No match** means "No relationship found in the indexed documents".
- **`rel-rules/1` → `rel-rules/2`:** a library recomputes relationships at its next catalogue change (a publish,
  archive, restore or manual link), not at upgrade: derived state is keyed to the catalogue sequence, and an upgrade
  does not move it. Until then the current generation keeps `rel-rules/1` and says so in its `rule_version`, while
  `GET /capabilities` reports the running rules (`relationship_schema_version: rel-rules/2`). For a library of ADF and
  Power BI documents the two versions give the same results except one case: an ADF binding is no longer compared with
  an Analysis Services endpoint (`rel-rules/1` could match one on the object name alone). SQL Agent documents are
  published after the upgrade, and that publication is a catalogue change, so their relationships use `rel-rules/2`.

## Generations and history

- **What a generation pins:** the exact revision vector, the detected relationships, and every manual assertion with its version.
- **Current views:** use the newest generation, marked `updating` if the catalogue has moved past it.
- **Historical views:**
  - `revision_id` alone selects the newest generation containing that revision;
  - `generation_id` pins a specific generation, whose state is then `pinned`;
  - a generation that doesn't contain the revision returns `EVIDENCE_UNAVAILABLE`, never current evidence.

## Manual relationships

- **Who and what:** publishers only. Each link has a kind and a reason and is labelled `user_asserted`; it is never merged into detected evidence.
- **Creating:** requires the current catalogue sequence and the current revisions (`409 SELECTION_STALE` otherwise) and existing objects (`422`).
- **Changes:** need `If-Match`, are audited (`create`, `update`, `delete` tombstone) and advance the sequence.
- **Status:** computed from the current revisions:
  - `active`;
  - `needs_review` when either object is gone from a complete snapshot (never reassigned by name);
  - `archived_target`.
- **Access:** archived counterparts are hidden from viewers.
