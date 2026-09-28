# Library HTTP API v1 (B05, B06)

OpenAPI: `docs/openapi-v1.json` (regenerate with `python -m bidoc_library.openapi > docs/openapi-v1.json`; CI fails if it is stale). Served at `/api/v1/openapi.json`.

## Run it

```
pip install -r requirements.txt
LOCAL_DATA_DIR=./bidoc-data python -m bidoc_library          # http://127.0.0.1:8765, local mode
```

| Variable | Default | Notes |
|---|---|---|
| `DATA_BACKEND` | `local` | `local` (SQLite plus files) or `azure` (Table Storage plus Blob; see `docs/azure-storage.md`) |
| `AZURE_STORAGE_TABLE_ENDPOINT`, `AZURE_STORAGE_BLOB_ENDPOINT` | — | `azure` with a managed identity: `https://<account>.table.core.windows.net`, `https://<account>.blob.core.windows.net` |
| `AZURE_CLIENT_ID` | — | a user-assigned managed identity; otherwise `DefaultAzureCredential` |
| `AZURE_STORAGE_CONNECTION_STRING` | — | `azure` with a connection string instead (Azurite, development). Give either this or both endpoints |
| `AZURE_STORAGE_TABLE`, `AZURE_STORAGE_CONTAINER` | `bidoc`, `bidoc` | created if missing |
| `LOCAL_DATA_DIR` | `bidoc-data` | persistent local disk; one library process per folder |
| `AUTH_MODE` | `local` | `local`, `gateway` or `entra`. There is never an unauthenticated fallback |
| `BIND_HOST`, `PORT` | `127.0.0.1`, `8765` | local mode refuses any non-loopback address |
| `GATEWAY_TRUSTED_PROXIES` | — | required in gateway and entra modes: addresses/CIDRs allowed to assert identity |
| `ENTRA_TENANT_ID` | — | required in entra mode: the directory ID; sign-ins from other tenants get `401` |
| `ENTRA_ROLE_MAP` | `BiDoc.Viewer=viewer,BiDoc.Publisher=publisher,BiDoc.Admin=admin` | Entra app role values to library roles |
| `ENTRA_DEFAULT_ROLES` | — | roles for a signed-in user with no app role; empty means `403` |
| `GATEWAY_SUBJECT_HEADER`, `GATEWAY_ROLES_HEADER`, `GATEWAY_DEFAULT_ROLES` | `X-Forwarded-User`, `X-Forwarded-Roles`, `viewer` | roles are `viewer`, `publisher`, `admin` |
| `MAX_HTML_BYTES`, `MAX_MANIFEST_BYTES` | 25 MiB, 16 MiB | pilot defaults |
| `MAX_ZIP_BYTES` | 100 MiB | ZIP profile uploads (see `docs/contracts/envelope-v1.md`) |
| `CLIENT_SEARCH_INDEX_BYTES` | 8 MiB | above this, browsers use server search (`docs/performance.md`) |
| `MAX_SOURCE_BYTES` | 1 GiB | processing-job source uploads (R3, `docs/workers.md`) |
| `SOURCE_RETENTION_HOURS` | 24 | raw job sources kept after a job ends (1–720) |
| `ALLOWED_HOSTS` | — | extra Host values accepted in local mode |

## Access

- **local:** one local owner with every role.
  - The Host header must be a loopback name, which blocks DNS rebinding.
  - A cross-site `Origin` is refused.
  - Changes need `X-Bidoc-Session: <LOCAL_DATA_DIR/session-secret>` and `X-Requested-With: bidoc`.
- **gateway:** identity headers are trusted only from `GATEWAY_TRUSTED_PROXIES`; anything else gets `401`. Changes need `X-Requested-With: bidoc`, which a cross-site form or simple request cannot send.
- **entra:** Azure Container Apps or App Service built-in authentication signs the user in, and the library reads the validated `X-MS-CLIENT-PRINCIPAL`, only from `GATEWAY_TRUSTED_PROXIES` and only for `ENTRA_TENANT_ID`. The subject is the Entra object ID. Changes need `X-Requested-With: bidoc`. See `docs/azure-deployment.md`.
- **Errors:** `401` means missing or invalid identity; `403` means insufficient role. Archived documents are visible only to publishers.

## Routes

Processing jobs and workers (R3) are in `docs/workers.md`.

| Method and path | Role | Success / main errors |
|---|---|---|
| `GET /health/live`, `GET /health/ready` | none | `200`; ready `503 NOT_READY` (no details) |
| `GET /capabilities` | viewer | manifest versions, limits, access mode, `can_publish`, `can_manage_relationships`, `relationship_schema_version`, `processing` per engine and `worker_status` (`ready`, `unavailable`, `none_enrolled`; see `docs/workers.md`), installer availability, `search_available`, `search_mode` |
| `GET /documents` | viewer (`archived=true`: publisher) | `{items, next_cursor}`; filters `q`, `document_type`, `business_area`, `environment`, `owner`, `tag`; `limit` 1–200 |
| `GET /documents/{id}` | viewer | document plus `ETag` header; `404` |
| `GET /documents/{id}/revisions` | viewer | committed history, newest first |
| `GET /documents/{id}/revisions/{rev}/download` | viewer | stored safe artifact as an attachment |
| `GET /documents/{id}/revisions/{rev}/view` | viewer | stored artifact under `CSP: sandbox allow-scripts; default-src 'none'; connect-src 'none'; form-action 'none'; …` (no `allow-same-origin`). The library shell and navigation arrive in B06 |
| `POST /imports` | publisher | multipart `file`, optional `target_document_id`, `query_code` (`withheld` default or `included`), optional `legacy_metadata` (legacy input only); headers `Idempotency-Key` (required) and `If-Match` (for updates). `201` new, `200` duplicate; `400`, `409`, `411`, `413`, `422` (`CONTRACT_INVALID`, `UNSUPPORTED_SAFE_PROJECTION`), `428` |
| `POST /imports/preview` | publisher | same `file`, `query_code` and `legacy_metadata`; returns input kind (`envelope`/`legacy`), outcome (`new_document`, `new_version`, `duplicate`), title, classification, native schema, omissions and coverage warnings. Stores nothing |
| `GET /imports/{id}` | publisher | durable import state |
| `GET /documents/{id}/metadata` | viewer | effective metadata, the current revision's own metadata, override provenance (who, why, when, sequence), immutable stream fields; `ETag` |
| `PATCH /documents/{id}/metadata` | publisher | `If-Match` required. Body: any of `title`, `description`, `tags`, `business_area`, `owner` (`null` removes an override) plus required `reason`. Stream fields give `422 IMMUTABLE_FIELD`. Artifacts are never modified; the catalogue sequence advances; overrides survive new revisions |
| `GET /documents/{id}/metadata/history` | publisher | every override change with before/after values and its catalogue sequence |
| `POST /documents/{id}/archive`, `/restore` | publisher | `If-Match` required; `200` with new `ETag`; `409`, `428` |
| `GET /search-index` | viewer | `{generation, state, documents:[{…, sections}]}` for current active revisions; `ETag` + `If-None-Match` → `304`. State `ready`, `updating` or `stale` (prior snapshot, labelled) |
| `GET /search` | viewer | server-side search with the same semantics (`bidoc_library/search.py`): `q`, type and classification filters, `tag` |
| `GET /documents/{id}/objects` | viewer | objects of a revision (default current), with section anchor and view target, plus `sections` (view targets for sections without an object, e.g. overview and pages) |
| `GET /documents/{id}/relationships` | viewer | `revision_id`, `generation_id`, `object_id` (a pipeline also matches its activities); returns generation (`ready`, `updating`, `pinned`, `evidence_unavailable`), `incoming`/`outgoing` detected links with confidence and evidence, and `manual` links with status |
| `POST /relationships/manual` | publisher | source and target document/revision/object, `kind` (`related_to`, `produces`, `consumes`, `deletes`), `reason`, `expected_catalogue_sequence`; `201` + `ETag`; `409 SELECTION_STALE`; `422 OBJECT_NOT_FOUND` |
| `GET`, `PATCH`, `DELETE /relationships/manual/{id}`, `GET …/audit` | viewer (read) / publisher | changes need `If-Match`; delete is a tombstone; every change is audited and advances the catalogue sequence |
| `GET /releases/latest`, `/releases/{v}/download` | viewer | `404 NO_APPROVED_RELEASE` until B11 |

- Every error is `{"error": {"code", "message", "request_id", "details"}}`, and the same request ID is returned in `X-Request-ID`.
- Unexpected failures return `500 INTERNAL_ERROR` with no paths or stack traces. Details go to the server log only.
- Upload limits are checked from `Content-Length` before the body is parsed (`411` without one).

## Legacy import (B07)

HTML made by pbi-doc-gen or adf-doc-gen before the platform existed has no manifest. The
library converts it when, and only when, it can do so safely (`bidoc_engines/legacy.py`):

- The page must contain exactly one `const DATA = ` literal and exactly one engine metadata
  element. The literal is decoded as JSON; no script is run.
- The native schema must be supported (`pbi-doc-gen/2`, `adf-doc-gen/2`).
- The payload is projected with the shared profile, with query code always withheld, and
  regenerated by the trusted renderer. The original bytes are not stored; only their hash
  is kept, for duplicate detection.
- Unless `target_document_id` names an existing document, the result is a new document
  with a new stream identity. Catalogue fields come from `legacy_metadata`
  (`title`, `description`, `tags`, `business_area`, `environment`, `owner`).
- Details entered in the old page (report location, account references, factory details)
  are not carried over. The preview says so.
- Anything else, including unknown HTML, is refused with `422 UNSUPPORTED_SAFE_PROJECTION`.

## Published viewers are read-only (A37)

Every stored artifact is rendered with `published: true`. In that mode the engines show
report and factory details as text, with a note to edit metadata in the library or
regenerate. They offer no "Download updated HTML". Local edits reach the library as a new
generated revision (`bidoc generate` with a new title, description or tags), which goes
through the normal ETag and revision rules.

## Not yet

- Publish tokens and direct desktop publishing (R2).

## Library shell

`GET /` serves the shell (see `docs/library-ui.md`) and `GET /static/{file}` its whitelisted assets. Both need the viewer role, like the API.
