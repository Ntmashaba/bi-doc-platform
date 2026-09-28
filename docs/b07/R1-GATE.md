# R1 gate: acceptance evidence (B07)

Every R1 acceptance ID maps to automated evidence that runs in CI on every push. Paths are
relative to the repository root. Test names are `file::Class.test` or `file::test`.

**CI jobs**
- `tests`: all unit and end-to-end suites; skips fail the job.
- `real-pbix-samples`: 29 real reports from pbi-tools/pbix-samples.
- `adf-templates`: 95 real ARM templates from Azure/Azure-DataFactory.
- `frontend`: compiled output check plus the Chromium acceptance run.
- `docker`: image build and container checks.

## Handoff section 11 (A01–A10)

| ID | Evidence | Job |
|---|---|---|
| A01 | `apps/library/tests/check_real_library.py` publishes the 29 real reports through the HTTP API. For every report with measures (26), it searches a measure name, requires a hit on that measure's own section, and opens the viewer registering `pbi.measure`. Synthetic PBIP/TMDL/BIM: `packages/engines/tests/test_generate.py`, `apps/library/tests/test_derived.py::test_index_search_and_filters`, and the browser step "selecting a measure opens it in the viewer" | real-pbix-samples, tests, frontend |
| A02 | `apps/library/tests/test_store.py::test_new_revision_needs_current_etag_and_keeps_history`, `test_api.py::test_update_needs_etag`, `test_derived.py::test_new_revision_rebuilds_and_history_stays_pinned` (search follows the new revision) | tests |
| A03 | `apps/library/tests/check_docker.py`: restart, then remove and re-create on the same volume; catalogue, artifact bytes, session secret and search remain | docker |
| A04 | `test_store.py::test_duplicates_and_idempotency`, `::test_same_revision_different_bytes_conflicts`, `test_api.py::test_idempotency_and_duplicates` | tests |
| A05 | `test_store.py::test_concurrent_updates_one_wins` (threads, same ETag) | tests |
| A06 | `test_store.py::test_crash_after_artifact_write`, `::test_crash_after_prepare_or_inside_commit_recovers_on_restart`, `::test_crash_after_commit_does_not_hide_the_publication`, `::test_recent_interrupted_work_is_left_alone` | tests |
| A07 | `apps/generator/tests/test_cli.py::test_doctor`: PBIX reported unavailable with a diagnosis, while PBIP/TMDL/BIM generation and the library run without Power BI tools | tests |
| A08 | **Deferred to B08** (batch generation) | — |
| A09 | Browser step "viewer is isolated": the sandboxed frame cannot read parent cookies or DOM, fetch, or navigate the top window; legitimate navigation works. `test_api.py::test_import_read_download_and_view` checks the view CSP | frontend, tests |
| A10 | HTML profile only: `test_store.py::test_invalid_and_unsupported_artifacts`, `test_api.py::test_malformed_and_invalid_uploads`, `::test_upload_limit`, contract suite (`packages/contracts/tests/test_contract.py`). **ZIP archives are deferred to B12** | tests |

## Handoff section 13 (A19–A28, R1 subset)

| ID | Evidence | Job |
|---|---|---|
| A19 | `test_derived.py::test_index_search_and_filters`, `test_frontend.py::test_same_results_as_python`; browser step "browse, filter and search" | tests, frontend |
| A20 | `packages/relationships/tests/test_rules.py::test_exact_static_producer`, `test_derived.py::test_bidirectional_exact_link`; browser steps "Power BI source shows its exact upstream pipeline", "link opens the exact ADF activity…", "Back restores the origin" | tests, frontend |
| A21 | `test_rules.py::test_contradictions_never_join`, `::test_missing_location_is_possible_at_most`, `::test_dynamic_or_opaque_never_exact`, `::test_delete_and_read_are_never_producers` | tests |
| A22 | `test_rules.py::test_letter_case`, `::test_files_and_folders` | tests |
| A23 | `test_derived.py::test_new_revision_rebuilds_and_history_stays_pinned`, `::test_stale_rebuild_cannot_overwrite_newer` | tests |
| A24 | `test_derived.py::test_lifecycle_audit_and_pinning` (publisher-only, preserved across regeneration, Needs review, no fuzzy reassignment) | tests |
| A25 | `packages/engines/tests/test_convert.py` (identity and flags kept, `UnsupportedProjection`), `test_legacy.py`, `apps/library/tests/test_legacy_import.py` (unsupported → `422 UNSUPPORTED_SAFE_PROJECTION`, original not stored), `test_generate.py::test_selection_is_its_own_stream`, `check_adf_templates.py` (95 real templates) | tests, adf-templates |
| A27 | Browser step "viewer is isolated; forged messages are ignored" (wrong source, channel or revision is ignored; messages carry no HTML or URLs); engine `tests/test_viewer_bridge.py` (parent-only listener, registered views) | frontend, engine CI |

## Handoff section 17 (A29–A40, R1 subset)

| ID | Evidence | Job |
|---|---|---|
| A29 | `test_generate.py::test_a29_every_representation` (HTML, manifest, search text), `test_projection.py`, `test_legacy.py::test_both_engines_convert_with_code_withheld`, `test_legacy_import.py` (no seeded marker in stored bytes); the real-data jobs check that shared output holds no query code | tests, real-pbix-samples, adf-templates |
| A30 | `test_rules.py::test_contradictions_never_join`, `::test_letter_case`, `::test_default_port_equals_no_port`; engine port/case tests in adf-doc-gen | tests, engine CI |
| A31 | `packages/engines/tests/test_a31_bindings.py`: one parameterised dataset invoked by two Copy activities binds A→B and C→D separately, and adding a table and source leaves every existing object ID unchanged. Also `test_generate.py::test_objects_bindings_and_search_text` and `::test_lineage_tags_are_object_ids_and_sources_are_logical`. Source objects are logical (one per source, however many pages use it), and page sections are keyed by page ID. A test that adds a report page is still to write (report fixtures arrive with B08) | tests |
| A32 | `test_generate.py::test_selection_is_its_own_stream`, `::test_incomplete_read_is_local_only`, `test_contract.py::test_adf_factory_and_selection_are_different_streams`, `::test_snapshot_must_be_complete`, `test_store.py::test_stream_and_type_are_immutable` | tests |
| A33 | `test_derived.py::test_new_revision_rebuilds_and_history_stays_pinned`, `::test_lifecycle_audit_and_pinning` (`409 SELECTION_STALE`) | tests |
| A34 | `test_store.py::test_intervening_commit_is_never_overwritten`, the crash tests under A06, `::test_duplicates_and_idempotency` | tests |
| A35 | Stored artifacts are always re-rendered by the trusted renderer (`test_convert.py`). The view is served sandboxed without same-origin (`test_api.py::test_import_read_download_and_view`). Host and Origin checks: `test_api.py::test_host_and_origin`, `::test_mutations_need_session_secret_and_csrf_header`, `::test_identity_only_from_the_gateway`, and the Docker Host check. Browser isolation probe as A09 | tests, frontend, docker |
| A37 | Engines 0.4.0 (`tests/test_viewer_bridge.py` in both repos); `test_convert.py::test_published_copy_is_marked_read_only`; browser step "legacy import converts; published viewer is read-only": no edit fields and no download button in the published viewer. Local edits become a new revision by regenerating: `test_metadata.py::test_null_removes_override_and_new_revision_keeps_overrides` publishes a regenerated revision with a new title | engine CI, tests, frontend |
| A40 | `apps/library/tests/test_metadata.py`: artifact bytes unchanged after overrides; provenance and history kept; stream fields refused with `422 IMMUTABLE_FIELD`; overrides survive new revisions; migration backfill | tests |

## Out of R1 (tracked elsewhere)

- A08 is in B08.
- A10 ZIP handling is in B12.
- A11–A18 and A26, A28, A36, A38, A39 and A41 belong to later backlog items (see the handoff's acceptance mapping).
- W2, real PBIX extraction on Windows, is waiting on the owner.
