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

## Detected relationships `rel-rules/1`

Package `packages/relationships` (pure, shared with the offline export later).

| Situation | Result |
|---|---|
| ADF write; SQL server, port, instance, database, schema and object all known and equal as written; both statically resolved | `produces`, `exact_static` |
| ADF file write to the same account, container and path | `produces`, `exact_static` |
| Location part missing on one side; letter-case-only difference; folder containing the file; dynamic, opaque or partial resolution | `possible` |
| ADF delete / ADF read of the same endpoint | `deletes` / `reads`, never `produces` |
| Different known server, port, instance, database, account, container or environment | no relationship |
| Environment undeclared on either side | allowed, with the note "environment not declared on both documents" |

- **Stored once:** each relationship is stored with source = ADF activity (and its parent pipeline) and target = Power BI source, and is served in both directions.
- **Evidence:** raw endpoints, both resolutions, environments, binding IDs and notes.
- **No match** means "No relationship found in the indexed documents".

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
