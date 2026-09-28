# Shared publication projection `shared-projection/1`

Status: frozen for B03 (pre-release). Governs what leaves a generator or enters the shared library. **Local documentation is never projected**: `profile: local` output keeps everything the engines produce today (ADR 0001).

## Options

| Option | Default (R1) | Meaning |
|---|---|---|
| `query_code` | `withheld` | M and SQL text. `withheld` replaces every field below with the marker `[query code withheld]` and records an omission. `included` keeps the code as written, after the best-effort cleaning below. The UI says cleaning is not a guarantee. |

## Always applied in the shared profile, whatever the options

| Reason | Rule |
|---|---|
| `machine_path` | The input's own path is removed (pbi-doc-gen `/model/sourcePath`). Personal local paths anywhere else (drive-letter paths, `file://` URIs to them, `/home`, `/Users`, `/root`), including inside code, labels and identifiers, become `<file name> — personal location withheld [ref <12 hex>]`. See *Personal paths* below. |
| `credential` | Connection-string secrets (`Password=`, `Pwd=`, `AccountKey=`, `SharedAccessSignature=` …) replaced with `[credential withheld]` in every string. |
| `secret_bearing_url` | URL query values for credential-like names (`sig`, `token`, `access_token`, `code`, `key`, `apikey`, `api_key`, `password`, `secret`, `client_secret`) replaced in every string. |
| `entered_data` | In retained M: `Binary.FromText("…")` bodies and literal row lists of `Table.FromRows({…})` / `#table(…, {…})` replaced with a withheld marker. |

These rules are a safety net over every string in the payload, including when `query_code` is `included`. They are pattern-based and are **not** a guarantee that no sensitive value remains. Only withholding code gives that property for code fields.

## Personal paths (owner decision, 2026-09-28)

| Path | Shared output |
|---|---|
| The PBIX/PBIP being documented (`sourcePath`, `pbixSource`, report location) | withheld; local output and history keep it |
| A personal file data source, e.g. `C:\Users\Alice\Data\Budget.xlsx` | withheld; the file name and a stable reference are kept |
| An ADF repository file, e.g. `pipeline/LoadSales.json` | kept (relative) |
| UNC shares, SharePoint, Blob and ADLS locations | kept: real shared dependencies used for lineage |

- **The reference** is the first 12 hex digits of the SHA-256 of the normalised path
  (forward slashes, lower case). Distinct files stay distinct, even when their names match,
  and the same file keeps its reference across revisions. It is a pseudonym, not
  encryption: someone who already knows the exact full path can confirm a guess.
- **Endpoints:** a withheld path becomes `path: "withheld:<ref>"` in its endpoint.
  - Source object IDs derive from that, so they are opaque and independent of the
    displayed location.
  - Labels read "Budget.xlsx — personal location withheld (ref 871f53c0)".
  - Relationship rules never match a withheld path.
- **Local output is unchanged.** Local processing, such as batch re-use checks, keeps full
  paths.
- **Existing links:** sources that used to carry a personal path in their ID get a new ID
  on their next shared publication. Document and revision IDs do not change. Manual links
  to the old source ID follow the usual disappearance rule and show *Needs review*. Detected
  links never used personal paths, since file matching needs a storage account.

## Code-bearing fields (engines 0.2.0: pbi-doc-gen `b8d1fa1`, adf-doc-gen `c5645bf`)

**Always code, withheld by field name**, found by seeding markers through each engine:

| Engine | JSON Pointer pattern (`*` = any index) | Content |
|---|---|---|
| Power BI | `/sourceObjects/*/originalM`, `/sourceObjects/*/referencedM`, `/sourceQueries/*/mCode` | M |
| Power BI | `/sourceObjects/*/sql` | native SQL |
| Power BI | `/model/expressions/*/expression` | shared M queries and parameters |
| ADF | any `query`, `sqlReaderQuery`, `script`, `preCopyScript` field | SQL / scripts |

**Classified by content** (B03 correction). These fields hold M/SQL in some reports and DAX, a description or a source location in others. Measured on the 29 real reports:

| Field | Observed | Rule |
|---|---|---|
| `/tableSources/*/query` | 394 M/SQL, **557 DAX** calculated tables (`CALENDAR(...)`) | withheld only if it is M or SQL |
| `/tableSources/*/expression`, `/model/tables/*/partitions/*/expression`, `…/source/query` | M, SQL or DAX | withheld only if it is M or SQL |
| `/model/tables/*/partitions/*/source/detail` | **0 code**; 175 descriptions and source locations | kept; code inside would be caught |
| ADF `/pipelines/*/activities/*/detail`, `/lineageEdges/*/detail` | labels plus embedded SQL (`… \| pre-copy: DELETE FROM …`) | only the SQL segment is withheld; labels are kept |
| ADF data-flow `steps/*/config` | options, including `query:` / `preSQLs:` / `postSQLs:` | only those option values are withheld |

Content detection recognises:
- SQL statements (a select list with a table name, `INSERT INTO`, `UPDATE … SET`, `DELETE FROM`, `MERGE`, `TRUNCATE`, qualified `EXEC`, `CREATE`);
- SQL assembled in ADF expressions (`@concat('SELECT … FROM ', …)`);
- M (`let … in`, `#"…"`, namespace-qualified calls such as `Excel.Workbook(`).

DAX is never namespace-qualified, so it is not matched. Prose fields (descriptions, titles, names, labels, messages, notes) are never withheld by pattern.

**Honesty note.** The field-name rules are exact. The content rules are patterns, so a code fragment in an unexpected field and an unusual form could escape them. The repository checks this against every real input available: the 29 pre-extracted Microsoft reports and all 95 templates in Microsoft's Azure-DataFactory repository (`packages/engines/tests/check_*.py`, run in CI). Every new leak found becomes a test.

## Guarantees and test gate (A29)

- Projection runs before rendering, envelope creation, section/search text and every export. The rendered `const DATA`, `native_payload.data` and `sections` all come from the projected payload.
- With `query_code: withheld`, every seeded marker (password, SQL literal, entered rows, compressed body, URL token, pre-copy literal) is absent from HTML, manifest and search text, for both engines (`packages/engines/tests/test_generate.py`).
- With `query_code: included`, credential, URL-token and entered-data markers are absent. SQL literals remain, by design, and the omission list and UI make that visible.
- Each omission records a JSON Pointer and a reason, never the value.
- The importer re-applies this projection. A producer's `profile`/`options` labels are never trusted.
