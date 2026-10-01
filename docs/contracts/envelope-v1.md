# Publication envelope v1

Status: **frozen for B03 (pre-release)**, 28 September 2026. No artifact has been distributed yet. Any change from here is recorded in `docs/PROGRESS.md`, and once artifacts ship, changes need a new `schema_version`.

- Schema: `packages/contracts/bidoc_contracts/schema/envelope-v1.schema.json` (JSON Schema 2020-12).
- Validator, embed and hash: `packages/contracts/bidoc_contracts/` (standard library only). The validator interprets the schema file directly, so the schema file is the single source of field shapes. `tests/test_contract.py` cross-checks the validator against the reference `jsonschema` library.
- Golden fixtures: `packages/contracts/tests/fixtures/`, rebuilt by `tests/build_fixtures.py` (`--check` fails if stale).

## Additions beyond the handoff's field table

| Field | Why |
|---|---|
| `projection.profile` (`local` / `shared`) | ADR 0001: local generation and shared publication have separate, explicit policies. The importer re-applies the shared projection and never trusts this label. |
| `projection.options.query_code` (`included` / `withheld`) | ADR 0001 §1: *Include query code* is an explicit publisher choice. When withheld, code is absent from `native_payload`, sections (search text) and the rendered view, and each removed field appears in `projection.omissions`. |
| `projection.omissions[].reason` enum | `query_code_withheld`, `credential`, `entered_data`, `machine_path`, `secret_bearing_url`, `unsupported_field`. Paths are JSON Pointers into `native_payload.data`; values are never echoed. |
| `bindings[].normalized_endpoint` + `normalization_version` | Section 16: raw endpoint values are kept alongside a separate comparison form. |
| `publication.scope_key` derived from `scope_descriptor` | `bidoc_contracts.scope`: Power BI `model_and_report` / `model` / `report`; ADF `factory` or `selection-<sha256 of sorted resources>`. The validator rejects a mismatch, so scope cannot be relabelled. |
| `navigation.targets[]` = `{target_id, view_id, args}` | Section 17.1 validated target registry. `view_id` is checked against the adapter's registered views when the importer supplies them. |

## Validation order and error codes

1. Size: `ARTIFACT_TOO_LARGE` (25 MiB default). This is a *publication* limit: the generator keeps a document above it (hard generation ceiling 256 MiB) and reports it as local-only with the size and the limit; `publish` compares against the target library's advertised limit instead of the default.
2. Location: `MANIFEST_MISSING`, `MANIFEST_DUPLICATE`, `MANIFEST_UNTERMINATED`, `MANIFEST_NOT_INERT` (type must be `application/json`), `MANIFEST_TOO_LARGE` (16 MiB).
3. JSON: `MANIFEST_INVALID_JSON` (also duplicate keys and NaN/Infinity).
4. `UNSUPPORTED_SCHEMA_VERSION`, then `SCHEMA_VIOLATION` (field shapes, unknown fields).
5. `CONTRACT_VIOLATION`: unique section/object/binding/target IDs; object `section_id` and `parent_object_id` resolve; binding `evidence_refs` resolve in `native_payload.data`; navigation targets resolve; registered view IDs; `assets` only in the ZIP profile; combined section text ≤ 4 MiB.
6. `HASH_MISMATCH`: the handoff's rule; the whole manifest element, opening tag included, is replaced by `<!--PBIDOC-MANIFEST-->` before SHA-256.
7. `MISSING_ANCHOR`: every section ID must be an element `id` in the document, found by a non-executing HTML parse.

## ZIP profile (B12)

A ZIP artifact holds exactly one root `document.html` (an ordinary v1 envelope) plus files
under `assets/`. `manifest.assets` lists every asset as `{path, sha256}`, and the archive
must contain exactly those entries. `bidoc_contracts.validate_zip` refuses:

- paths that traverse, are absolute, use backslashes or drive letters, or have `.`, `..`
  or empty segments;
- duplicate or case-colliding entries, symlinks, encrypted entries, nested archives and
  unexpected entries;
- archives over 100 MiB zipped, 250 MiB expanded or 2000 entries (sizes are counted while
  reading, not trusted from headers); `MAX_ZIP_BYTES` sets the library's upload cap.

The library imports the document and, for now, does not keep the assets: the import adds
an `ASSETS_NOT_KEPT` coverage warning. Current engines produce single-file HTML.

## Producer rule

The producer renders its HTML with the placeholder `<!--PBIDOC-MANIFEST-->` exactly once, and `embed_manifest()` hashes those bytes and inserts the element. The manifest JSON escapes every `<` as `<`, so no text can close the script. The engines' existing `const DATA` blobs escape only `</`. B03's renderer changes should escape `<` there too, so a payload string that spells a manifest element cannot create a second manifest.

## Snapshot completeness

`snapshot_state` is always `complete`: the envelope is only attached to a complete snapshot of its declared scope. ADF output with skipped input files is not complete. The generator still writes local documentation, but without a manifest, and says it cannot be published. Unresolved references inside a completely read scope are coverage warnings (`projection.coverage_warnings`), not incompleteness.

See also `projection-v1.md` and `identity-and-bindings-v1.md`.

## Producer implementation notes (B03)

- `bidoc_engines.envelope.assemble` inserts the placeholder before `</head>`, and before `</body>` a hidden, script-free `<div id="pbidoc-sections">` holding each section as `<section id="pbidoc-…">`. Those elements are the anchors `MISSING_ANCHOR` checks, and they keep the search text readable without JavaScript. The finished artifact is validated before it is written.
- Section IDs are `pbidoc-<kind>-<first 16 hex of sha256(object_id)>`, so they stay stable across revisions.
- The projected native payload appears twice: once in the engine's `const DATA` (for its viewer) and once in `native_payload.data`. Real reports are 0.1–7.3 MB, under the 25 MiB limit. B06's trusted viewer can read the manifest instead and remove the duplicate.
- Power BI scope descriptors map the engine's `mode` values `combined` / `semantic-only` / `report-only` to `model_and_report` / `model` / `report`. The B02 mapping assumed `model` / `report` and was corrected in B03.
