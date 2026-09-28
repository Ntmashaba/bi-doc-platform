# Shared publication projection `shared-projection/1`

Status: frozen for B03 (pre-release). Governs what leaves a generator or enters the shared library. **Local documentation is never projected**: `profile: local` output keeps everything the engines produce today (ADR 0001).

## Options

| Option | Default (R1) | Meaning |
|---|---|---|
| `query_code` | `withheld` | M and SQL text. `withheld` replaces every field below with the marker `[query code withheld]` and records an omission. `included` keeps the code as written, after the best-effort cleaning below. The UI says cleaning is not a guarantee. |

## Always applied in the shared profile, whatever the options

| Reason | Rule |
|---|---|
| `machine_path` | Absolute machine paths removed (pbi-doc-gen `/model/sourcePath`). |
| `credential` | Connection-string secrets (`Password=`, `Pwd=`, `AccountKey=`, `SharedAccessSignature=` …) replaced with `[credential withheld]` in every string. |
| `secret_bearing_url` | URL query values for credential-like names (`sig`, `token`, `access_token`, `code`, `key`, `apikey`, `api_key`, `password`, `secret`, `client_secret`) replaced in every string. |
| `entered_data` | In retained M: `Binary.FromText("…")` bodies and literal row lists of `Table.FromRows({…})` / `#table(…, {…})` replaced with a withheld marker. |

These rules are a safety net over every string in the payload, including when `query_code` is `included`. They are pattern-based and are **not** a guarantee that no sensitive value remains. Only withholding code gives that property for code fields.

## Code-bearing fields (inventory at pbi-doc-gen `17e419c`, adf-doc-gen `5b57005`)

Found by seeding markers through each engine (`spikes/b01`), not by guessing:

| Engine | JSON Pointer pattern (`*` = any index) | Content |
|---|---|---|
| Power BI | `/model/tables/*/partitions/*/expression` | partition M |
| Power BI | `/model/tables/*/partitions/*/source/query`, `…/source/detail` | native query / source detail |
| Power BI | `/model/expressions/*/expression` | shared M expressions and parameters |
| Power BI | `/sourceObjects/*/originalM`, `/sourceObjects/*/sql` | M per source object; native SQL |
| Power BI | `/sourceQueries/*/mCode` | M per query |
| Power BI | `/tableSources/*/expression`, `/tableSources/*/query` | M / native SQL per table |
| ADF | `/pipelines/*/activities/*/query` | Lookup/Copy/Script SQL |
| ADF | `/lineageEdges/*/detail` when the mechanism carries a query | SQL copied into lineage detail |
| ADF | `/dataflows/*/script` (if present) | data-flow script |

DAX (measure and calculated-column expressions) is **not** query code. It stays in shared output, because it is the documentation's core content and does not reach data sources.

## Guarantees and test gate (A29)

- Projection runs before rendering, envelope creation, section/search text and every export. The rendered `const DATA`, `native_payload.data` and `sections` all come from the projected payload.
- With `query_code: withheld`, every seeded marker (password, SQL literal, entered rows, compressed body, URL token) is absent from HTML, manifest and search text, for both engines.
- With `query_code: included`, credential, URL-token and entered-data markers are absent. SQL literals remain, by design, and the omission list and UI make that visible.
- Each omission records a JSON Pointer and a reason, never the value.
- The importer re-applies this projection. A producer's `profile`/`options` labels are never trusted.
