# Shared publication projection `shared-projection/1`

Status: frozen for B03 (pre-release). Governs what leaves a generator or enters the shared library. **Local documentation is never projected**: `profile: local` output keeps everything the engines produce today (ADR 0001).

## Options

| Option | Default (R1) | Meaning |
|---|---|---|
| `query_code` | `withheld` | M and SQL text. `withheld` replaces every field below with the marker `[query code withheld]` and records an omission. `included` keeps the code as written, after the best-effort cleaning below. The UI says cleaning is not a guarantee. |

## Always applied in the shared profile, whatever the options

| Reason | Rule |
|---|---|
| `machine_path` | The input's own path is removed (pbi-doc-gen `/model/sourcePath`). Personal local paths anywhere else (drive-letter paths, `file://` URIs to them, `/home`, `/Users`, `/root`), including inside code, labels and identifiers, become `<file name> — personal location withheld [ref <SHA-256 hex>]`. See *Personal paths* below. |
| `credential` | Replaced with `[credential withheld]` in every string: connection-string secrets (`Password=`, `Pwd=`, `AccountKey=`, `SharedAccessSignature=` …); the value of a credential header — `Authorization`, `Proxy-Authorization`, `x-api-key`, `api-key`, `apikey`, `x-functions-key`, `Ocp-Apim-Subscription-Key`, `x-auth-token` — written as an M record field (`Authorization="Bearer …"`, `#"x-api-key"="…"`), a JSON member (`"Authorization": "…"`) or header text (`Authorization: Bearer …`); a literal joined to its scheme in M (`"Bearer " & "…"`); and a bearer token anywhere else (`Bearer <token>`). See *Credential patterns* below. |
| `secret_bearing_url` | URL query values for credential-like names (`sig`, `token`, `access_token`, `code`, `key`, `apikey`, `api_key`, `password`, `secret`, `client_secret`) replaced in every string. The user information of a URL (`scheme://user:password@host`, or a token passed as the user) becomes `scheme://[credential withheld]@host`; host, path and other query values are kept. |
| `entered_data` | In retained M: `Binary.FromText("…")` bodies and literal row lists of `Table.FromRows({…})` / `#table(…, {…})` replaced with a withheld marker. |

These rules are a safety net over every string in the payload, including when `query_code` is `included`. They are pattern-based and are **not** a guarantee that no sensitive value remains. Only withholding code gives that property for code fields.

## Credential patterns (extended 2026-10-08)

| Written in the file | Shared output |
|---|---|
| `Web.Contents("https://svc:Secr3t@api.contoso.com/v1/orders")` | `Web.Contents("https://[credential withheld]@api.contoso.com/v1/orders")` |
| `[Headers=[Authorization="Bearer eyJ…", #"x-api-key"="k-123…"]]` | `[Headers=[Authorization="[credential withheld]", #"x-api-key"="[credential withheld]"]]` |
| `{"Authorization": "Basic dXNl…"}` | `{"Authorization": "[credential withheld]"}` |
| `Authorization: Bearer eyJ…` (header text, a script, a description) | `Authorization: [credential withheld]` |
| `[Headers=[Authorization="Bearer " & Token]]` | unchanged: `Token` names a value held elsewhere |
| `Authorization: @{activity('Get token').output.access_token}` | unchanged: a Data Factory expression names a value, it is not the value |
| `abfss://raw@lake.dfs.core.windows.net/sales`, `wasbs://landing@acct.blob.core.windows.net/in/` | unchanged: a container and an account, not a user |
| "Uses bearer authentication against the warehouse API." | unchanged: a bearer token must look like one (a digit or token punctuation, eight characters or more) |

- The whole value of a credential header goes, scheme included, so a `Basic` value cannot be decoded and a custom scheme cannot leak.
- Everything before the last `@` of a URL's authority goes: a password may contain `@`, and a token is sometimes passed as the user with no password.
- **Stable when applied again.** A value that is already a marker is recognised and left alone, so the library re-projecting a stored shared payload changes nothing and reports no new omission (`test_projecting_again_changes_nothing`). Before 2026-10-08 a second pass over `Password=[credential withheld];` left `withheld];` behind.
- The patterns are the supported set. They are not a guarantee: a secret in a form not listed here (a key in a custom header name, a token built by concatenating fragments) stays as written when code is included. Only withholding code removes it with certainty.

## Personal paths (owner decision, 2026-09-28)

| Path | Shared output |
|---|---|
| The PBIX/PBIP being documented (`sourcePath`, `pbixSource`, report location) | withheld; local output and history keep it |
| A personal file data source, e.g. `C:\Users\Alice\Data\Budget.xlsx` | withheld; the file name and a stable reference are kept |
| An Analysis Services data source named after such a path, e.g. `File/C:\Users\Alice\Data\Budget.xlsx` | the path after the kind is withheld the same way, with the same reference: `File/Budget.xlsx — personal location withheld [ref …]`. A drive letter after `/` inside a URL or a longer path is left alone |
| An ADF repository file, e.g. `pipeline/LoadSales.json` | kept (relative) |
| UNC shares, SharePoint, Blob and ADLS locations | kept: real shared dependencies used for lineage |

- **The reference** is the SHA-256 of the normalised path (forward slashes, lower case).
  - **It is a pseudonymous identifier, not anonymisation.** Anyone who can guess the exact
    full path can confirm the guess. It is unkeyed by design for the pilot (owner decision).
  - **Identity uses the full digest.** Labels show its first 8 characters, or more when two
    sources in one document would otherwise read the same. A collision between short
    references never merges sources.
  - **Stability:** a file keeps its reference while its original path stays the same.
    Moving the file, or generating the document from another user's folder, changes the
    reference.
  - Distinct files stay distinct, even when their names match.
  - Re-projecting an already-redacted payload, for example when the library re-imports a
    shared artifact, changes nothing.
- **Endpoints:** a withheld path becomes `path: "withheld:<full digest>"` in its endpoint.
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
| Power BI | every other field of a `/sourceQueries/*` row (`kind`, `origin`, `table`, `usedBy`, `upstream`, `sources`, `group`, `load`, `extraction`, `steps.status` …) | facts about the query, never its text: published in both modes. Step names and step descriptions are read from `mCode` when the page is rendered, so they exist only where the code does |
| ADF | any `query`, `sqlReaderQuery`, `script`, `preCopyScript` field | SQL / scripts |

**Classified by content** (B03 correction). These fields hold M/SQL in some reports and DAX, a description or a source location in others. Measured on the 29 real reports:

| Field | Observed | Rule |
|---|---|---|
| `/tableSources/*/query` | 394 M/SQL, **557 DAX** calculated tables (`CALENDAR(...)`) | withheld only if it is M or SQL |
| `/tableSources/*/expression`, `/model/tables/*/partitions/*/expression`, `…/source/query` | M, SQL or DAX | withheld only if it is M or SQL |
| `/model/tables/*/partitions/*/source/detail` | **0 code**; 175 descriptions and source locations | kept; code inside would be caught |
| ADF `/pipelines/*/activities/*/detail`, `/lineageEdges/*/detail` | labels plus embedded SQL (`… \| pre-copy: DELETE FROM …`) | only the SQL segment is withheld; labels are kept |
| Any other Power BI string that is M or SQL | code | withheld whole. Only a Data Factory `detail` joins labels and code with ` \| `; an M filter such as `[Status] = "A \| B"` is one piece of code (before 2026-10-08 its second half was left in the shared payload) |
| ADF data-flow `steps/*/config` | options, including `query:` / `preSQLs:` / `postSQLs:` | only those option values are withheld |

Content detection recognises:
- SQL statements (a select list with a table name, `INSERT INTO`, `UPDATE … SET`, `DELETE FROM`, `MERGE`, `TRUNCATE`, qualified `EXEC`, `CREATE`);
- SQL assembled in ADF expressions (`@concat('SELECT … FROM ', …)`);
- M (`let … in`, `#"…"`, namespace-qualified calls such as `Excel.Workbook(`).

DAX is never namespace-qualified, so it is not matched. Prose fields (descriptions, titles, names, labels, messages, notes) are never withheld by pattern.

**Honesty note.** The field-name rules are exact. The content rules are patterns, so a code fragment in an unexpected field and an unusual form could escape them. The repository checks this against every real input available: the 29 pre-extracted Microsoft reports and all 95 templates in Microsoft's Azure-DataFactory repository (`packages/engines/tests/check_*.py`, run in CI). Every new leak found becomes a test.

## Guarantees and test gate (A29)

- Projection runs before rendering, envelope creation, section/search text and every export. The rendered `const DATA`, `native_payload.data` and `sections` all come from the projected payload.
- With `query_code: withheld`, every seeded marker (password, SQL literal, entered rows, compressed body, URL token, URL password, bearer token, API key, a literal containing ` | `, pre-copy literal) is absent from HTML, manifest and search text, for both engines (`packages/engines/tests/test_generate.py`).
- With `query_code: included`, credential, URL-token, URL user-information, authorization-header, API-key and entered-data markers are absent from the whole artifact (HTML, embedded payload, search index, manifest), for both engines. SQL and M literals remain, by design, and the omission list and UI make that visible.
- With the local profile nothing is projected: every seeded value is present and the omission list is empty (`test_local_output_is_not_projected`). The Data Factory engine's own redaction of secret settings is part of that engine and applies to local output as before.
- Content derived from the payload inside the document (the object index, and the Power Query view's step names, step descriptions and step expressions) is computed from the projected payload at render time, so it can hold nothing the payload does not. With `query_code: withheld` a query has no steps on the page; with `included` its steps are read from the cleaned script (`test_power_query_follows_the_publication_policy`). A step description never repeats a connection string, a native statement, URL user information or a URL query string, in any profile.
- In a Data Factory `detail`, a ` | ` inside a quoted literal does not end the withheld code (`test_a_separator_inside_a_literal_stays_withheld`).
- Each omission records a JSON Pointer and a reason, never the value.
- The importer re-applies this projection. A producer's `profile`/`options` labels are never trusted.
