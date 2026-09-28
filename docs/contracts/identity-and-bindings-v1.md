# Object identity and endpoint bindings v1 (B02 draft)

Engine revisions surveyed: pbi-doc-gen `a7d5565`, adf-doc-gen `7c8cfe5` (see `docs/b01/BASELINE.md` §5). IDs are scoped to a `document_id`. The algorithms below are versioned by `identity_version`; any change is a new version with a migration and a manual-link review report.

## `pbi-identity/1`

| Kind | object_id | Durable basis | Engine change needed (B03) |
|---|---|---|---|
| table | `pbi:table:{lineageTag}` | TMDL/BIM `lineageTag`, written by Power BI Desktop and stable across renames | Surface `lineageTag` from `tmdl_reader` and the BIM path of `model_parser` (both currently drop it; verified) |
| measure | `pbi:measure:{lineageTag}` | as above; `parent_object_id` = its table | as above |
| column (as `other`, R1 optional) | `pbi:column:{lineageTag}` | as above | as above |
| source | `pbi:source:{connector}:{server}:{port}:{database}:{schema}.{object}` from **raw** values, percent-encoding `:` | physical location, independent of page usage | Split logical sources from per-page usage rows in `sourceObjects` (§17.2) |
| page (section only) | section anchor, not an object | report page `id` (e.g. `ReportSection1`) | — |

**Fallback without `lineageTag`** (older BIM, legacy extracts): `pbi:table:name:{name}` and `pbi:measure:name:{table}/{name}`, with the sidecar mapping file persisting them. A rename without a durable tag is a new object and triggers Needs review; there is never fuzzy matching. Copied PBIX files share lineage tags. That is harmless because IDs are scoped per `document_id`, and a copy gets a new stream only by explicit choice.

## `adf-identity/1`

| Kind | object_id | Basis |
|---|---|---|
| pipeline / trigger / dataset / linked_service / dataflow | `adf:{kind}:{name}` | ADF resource names are unique per factory and kind |
| activity | `adf:activity:{pipeline}/{container path}/{activity}` | Activity names are unique within a pipeline, but nested containers (ForEach/If/Switch/Until) need the path; `parent_object_id` = the pipeline |
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
- Host: lowercase DNS name, strip scheme and `tcp:`; **keep port and instance** as separate fields (`sql1,1444` → server `sql1`, port 1444). No default-port inference: an unknown port stays `null` and makes an exact match impossible unless both sides are `null` with an explicit same-connector rule.
- SQL database/schema/object: **case preserved** (collation unknown); comparison may treat case-only differences as `possible`, never `exact_static`.
- Storage account/container: lowercase (Azure-defined case-insensitive). Path: case preserved, percent-encoding preserved (no decoding of `%2F`).
- The adf-doc-gen engine currently strips ports and lowercases SQL names inside `common.physical_key`. B03 must fix that in the engine, because a bridge-only fix cannot separate merged entities.
