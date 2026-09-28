# Library HTTP API v1 (B05)

OpenAPI: `docs/openapi-v1.json` (regenerate with `python -m bidoc_library.openapi > docs/openapi-v1.json`; CI fails if it is stale). Served at `/api/v1/openapi.json`.

## Run it

```
pip install -r requirements.txt
LOCAL_DATA_DIR=./bidoc-data python -m bidoc_library          # http://127.0.0.1:8765, local mode
```

| Variable | Default | Notes |
|---|---|---|
| `DATA_BACKEND` | `local` | `azure` arrives in B10 and is refused until then |
| `LOCAL_DATA_DIR` | `bidoc-data` | persistent local disk; one library process per folder |
| `AUTH_MODE` | `local` | `local` or `gateway`; `entra` is refused until B13. There is never an unauthenticated fallback |
| `BIND_HOST`, `PORT` | `127.0.0.1`, `8765` | local mode refuses any non-loopback address |
| `GATEWAY_TRUSTED_PROXIES` | — | required in gateway mode: ingress addresses/CIDRs allowed to assert identity |
| `GATEWAY_SUBJECT_HEADER`, `GATEWAY_ROLES_HEADER`, `GATEWAY_DEFAULT_ROLES` | `X-Forwarded-User`, `X-Forwarded-Roles`, `viewer` | roles are `viewer`, `publisher`, `admin` |
| `MAX_HTML_BYTES`, `MAX_MANIFEST_BYTES` | 25 MiB, 16 MiB | pilot defaults |
| `ALLOWED_HOSTS` | — | extra Host values accepted in local mode |

## Access

- **local:** one local owner with every role.
  - The Host header must be a loopback name, which blocks DNS rebinding.
  - A cross-site `Origin` is refused.
  - Changes need `X-Bidoc-Session: <LOCAL_DATA_DIR/session-secret>` and `X-Requested-With: bidoc`.
- **gateway:** identity headers are trusted only from `GATEWAY_TRUSTED_PROXIES`; anything else gets `401`. Changes need `X-Requested-With: bidoc`, which a cross-site form or simple request cannot send.
- **Errors:** `401` means missing or invalid identity; `403` means insufficient role. Archived documents are visible only to publishers.

## Routes

| Method and path | Role | Success / main errors |
|---|---|---|
| `GET /health/live`, `GET /health/ready` | none | `200`; ready `503 NOT_READY` (no details) |
| `GET /capabilities` | viewer | manifest versions, limits, access mode, `can_publish`, processing (none: R3), installer (none yet), search (B06) |
| `GET /documents` | viewer (`archived=true`: publisher) | `{items, next_cursor}`; filters `q`, `document_type`, `business_area`, `environment`, `owner`, `tag`; `limit` 1–200 |
| `GET /documents/{id}` | viewer | document plus `ETag` header; `404` |
| `GET /documents/{id}/revisions` | viewer | committed history, newest first |
| `GET /documents/{id}/revisions/{rev}/download` | viewer | stored safe artifact as an attachment |
| `GET /documents/{id}/revisions/{rev}/view` | viewer | stored artifact under `CSP: sandbox allow-scripts; default-src 'none'; connect-src 'none'; form-action 'none'; …` (no `allow-same-origin`). The library shell and navigation arrive in B06 |
| `POST /imports` | publisher | multipart `file`, optional `target_document_id`, `query_code` (`withheld` default or `included`); headers `Idempotency-Key` (required) and `If-Match` (for updates). `201` new, `200` duplicate; `400`, `409`, `411`, `413`, `422`, `428` |
| `GET /imports/{id}` | publisher | durable import state |
| `POST /documents/{id}/archive`, `/restore` | publisher | `If-Match` required; `200` with new `ETag`; `409`, `428` |
| `GET /releases/latest`, `/releases/{v}/download` | viewer | `404 NO_APPROVED_RELEASE` until B11 |

- Every error is `{"error": {"code", "message", "request_id", "details"}}`, and the same request ID is returned in `X-Request-ID`.
- Unexpected failures return `500 INTERNAL_ERROR` with no paths or stack traces. Details go to the server log only.
- Upload limits are checked from `Content-Length` before the body is parsed (`411` without one).

## Not in B05

- Search index and relationships (B06).
- Metadata overrides (`PATCH /documents/{id}/metadata`).
- Converting existing engine HTML that has no manifest (legacy import). It is currently rejected with `422 CONTRACT_INVALID`; the conversion path is planned with B06/B07 using the B01 native-payload extraction spike.
- Publish tokens and direct desktop publishing (R2).
