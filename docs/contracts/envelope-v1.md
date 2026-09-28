# Publication envelope v1

Status: **draft for B02**. Not yet shipped. It may change until the first artifact is distributed; after that, changes need a new `schema_version`.

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
| `navigation.targets[]` = `{target_id, view_id, args}` | Section 17.1 validated target registry. `view_id` is checked against the adapter's registered views when the importer supplies them. |

## Validation order and error codes

1. Size: `ARTIFACT_TOO_LARGE` (25 MiB default).
2. Location: `MANIFEST_MISSING`, `MANIFEST_DUPLICATE`, `MANIFEST_UNTERMINATED`, `MANIFEST_NOT_INERT` (type must be `application/json`), `MANIFEST_TOO_LARGE` (16 MiB).
3. JSON: `MANIFEST_INVALID_JSON` (also duplicate keys and NaN/Infinity).
4. `UNSUPPORTED_SCHEMA_VERSION`, then `SCHEMA_VIOLATION` (field shapes, unknown fields).
5. `CONTRACT_VIOLATION`: unique section/object/binding/target IDs; object `section_id` and `parent_object_id` resolve; binding `evidence_refs` resolve in `native_payload.data`; navigation targets resolve; registered view IDs; `assets` only in the ZIP profile; combined section text ≤ 4 MiB.
6. `HASH_MISMATCH`: the handoff's rule; the whole manifest element, opening tag included, is replaced by `<!--PBIDOC-MANIFEST-->` before SHA-256.
7. `MISSING_ANCHOR`: every section ID must be an element `id` in the document, found by a non-executing HTML parse.

## Producer rule

The producer renders its HTML with the placeholder `<!--PBIDOC-MANIFEST-->` exactly once, and `embed_manifest()` hashes those bytes and inserts the element. The manifest JSON escapes every `<` as `<`, so no text can close the script. The engines' existing `const DATA` blobs escape only `</`. B03's renderer changes should escape `<` there too, so a payload string that spells a manifest element cannot create a second manifest.
