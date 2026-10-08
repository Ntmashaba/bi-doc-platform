# Object identity and endpoint bindings v1

Status: frozen for B03 (pre-release).

Engine revisions surveyed: pbi-doc-gen `a7d5565`, adf-doc-gen `7c8cfe5` (see `docs/b01/BASELINE.md` §5). IDs are scoped to a `document_id`. The algorithms below are versioned by `identity_version`; any change is a new version with a migration and a manual-link review report.

## `pbi-identity/1`

| Kind | object_id | Durable basis | Engine change needed (B03) |
|---|---|---|---|
| table | `pbi:table:{lineageTag}` | TMDL/BIM `lineageTag`, written by Power BI Desktop and stable across renames | Surface `lineageTag` from `tmdl_reader` and the BIM path of `model_parser` (both currently drop it; verified) |
| measure | `pbi:measure:{lineageTag}` | as above; `parent_object_id` = its table | as above |
| column (as `other`, R1 optional) | `pbi:column:{lineageTag}` | as above | as above |
| source | `pbi:source:{connector}:{server}:{port}:{database}:{schema}.{object}` from **raw** values, percent-encoding `:` | physical location, independent of page usage | Split logical sources from per-page usage rows in `sourceObjects` (§17.2) |
| page (section only) | section anchor, not an object | report page `id` (e.g. `ReportSection1`) | — |

**Implemented in B03:** pbi-doc-gen 0.2.0 surfaces `lineageTag` from TMDL and BIM. Source IDs use raw endpoint values, with `:` and `%` percent-encoded.

**Fallback without `lineageTag`** (older BIM, legacy extracts): `pbi:table:name:{name}` and `pbi:measure:name:{table}/{name}`, with the sidecar mapping file persisting them. A rename without a durable tag is a new object and triggers Needs review; there is never fuzzy matching. Copied PBIX files share lineage tags. That is harmless because IDs are scoped per `document_id`, and a copy gets a new stream only by explicit choice.

### Identity inside the document (2026-10-08)

The generated Power BI page keys its search index and its object links on the same ids, built by one module
(`pbidocgen/object_index.py`) that the adapter imports. Table and measure ids are unchanged, so manifest objects,
navigation targets and existing library links resolve as before. The additional ids below exist only inside the
document (index entries and `#o/<id>` links); they are **not** manifest objects, and `identity_version` stays
`pbi-identity/1`.

| Kind | id | Basis |
|---|---|---|
| column, calculated column | `pbi:column:{lineageTag}`, else `pbi:column:name:{table}/{column}` | as tables and measures |
| query (a partition's M or a shared expression) | `pbi:query:{lineageTag}` when the file gives the expression a tag, else `pbi:query:name:{query name}` | the query inventory's name |
| page | `pbi:page:{page id}` | the report's page id |
| visual | `pbi:visual:{page id}/{visual id}` | the report's ids |
| data source | `pbi:datasource:{16 hex}`: SHA-256 of type, server, database, schema, object and location | opaque; never spells a location, and changes when the source's identity changes |
| security role | `pbi:role:{name}` | role names are unique in a model |

Two objects that would share an id (a hand-edited model with a repeated tag) keep an entry each: the later one
gets `~2`, `~3`. An object link whose id is no longer in the document opens the Overview and says so.

## `adf-identity/1`

| Kind | object_id | Basis |
|---|---|---|
| pipeline / trigger / dataset / linked_service / dataflow | `adf:{kind}:{name}` | ADF resource names are unique per factory and kind |
| activity | `adf:activity:{pipeline}/{activity}` | ADF requires activity names to be unique within a pipeline, nested containers included, so no container path is needed; `parent_object_id` = the pipeline |
| file/table endpoint | not an object; endpoints live in bindings | — |

## Bindings

One binding per (object, operation, endpoint). An activity that reads A and writes B has two bindings. Engine output mapping (adf-doc-gen `lineageEdges`):

| Engine field | Binding |
|---|---|
| `sources[]` | `operation: read` |
| `sinks[]` with `mechanism != "Delete"` | `operation: write` |
| `sinks[]` with `mechanism == "Delete"` | `operation: delete` (never `write`; B01 probe showed the engine emits Delete as a sink) |
| stored-procedure / script activity with unknown footprint | `operation: execute`, `resolution: opaque` |
| `dynamic: true` / `opaque: true` | `resolution: dynamic` / `opaque` |

`evidence_refs` point to the native payload (`/pipelines/{i}/activities/{j}`, `/sourceObjects/{k}`), never copies.

## Endpoint normalization `endpoint-norm/1` (to be implemented in `packages/relationships`, B03/B06)

- `endpoint` keeps raw values exactly. `normalized_endpoint` is the comparison form.
- Host: lowercase DNS name, strip scheme and `tcp:`; **keep port and instance** as separate fields (`sql1,1444` → server `sql1`, port 1444).
- Connector rule (B03): for SQL Server-family systems, a host with no port and no named instance uses the default port 1433, so `normalized_endpoint.port` is 1433. `sql1` and `sql1,1433` compare equal, while `sql1,1444` is a different server. Other connectors get no default: an unknown port stays `null`.
- SQL database/schema/object: **case preserved** (collation unknown); comparison may treat case-only differences as `possible`, never `exact_static`.
- Storage account/container: lowercase (Azure-defined case-insensitive). Path: case preserved, percent-encoding preserved (no decoding of `%2F`).
- Fixed in adf-doc-gen 0.2.0 (B03): `common.physical_key` keeps non-default SQL ports and SQL letter case, and the bridge no longer treats deletes as producers, folds path case, or calls a folder prefix exact.

## Identity sidecar

`<source name>.bidoc-identity.json` beside the source, never inside it: ADF reads every JSON file in an input folder. It records the path it was created for. If a source is copied or moved, the generator stops with `IDENTITY_DECISION_REQUIRED` until the user chooses `existing` or `new`. When the source's folder is not writable, the mapping is kept in the generator's local mapping folder. The environment label is normalised to `environment_key` (`prod` → `production`, and so on) and is part of the stream.
