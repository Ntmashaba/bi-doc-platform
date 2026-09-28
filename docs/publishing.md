# Direct publishing and installer downloads (B11)

The manual path, generating a file and then importing it in the library, stays available.
Direct publishing adds a scoped credential so the generator can publish without a browser
session.

## Publishing tokens

In the library, go to **Publishing tokens** (publishers only), name the token, and choose
how long it lasts (1–30 days).

- **Format and storage:** the token is `bidocpt_<id>_<secret>`, with 256 random bits. It is
  shown once. The library stores only its SHA-256 hash.
- **Scope:** publishing only. The token authenticates `/api/v1/publishing/*` and nothing
  else. It cannot issue tokens, archive, edit metadata or manage relationships. Presented
  anywhere else, it gets `401`.
- **Revocation:** takes effect on the next request. Owners revoke their own tokens;
  administrators can revoke anyone's, and every action is audited.
- **Removing someone's publisher role:** the library cannot see role changes made in the
  identity provider (gateway mode), so an administrator calls
  `POST /api/v1/publish-tokens/revoke-subject {"subject": "..."}`. That revokes every active
  token of that person. Make this call part of the offboarding checklist; it is the central
  revocation policy that handoff 17.5 asks for.
- **Failures:** unknown, wrong, expired and revoked tokens all get the same `401`.

| Route | Authentication | Purpose |
|---|---|---|
| `POST /publish-tokens` | browser identity, publisher, CSRF header | issue `{label, expires_in_days}`; the token is returned once |
| `GET /publish-tokens` (`?all=true` for admins) | browser identity, publisher | own tokens, state `active`/`expired`/`revoked` |
| `DELETE /publish-tokens/{id}` | owner or admin | revoke |
| `POST /publish-tokens/revoke-subject` | admin | revoke all tokens of a subject |
| `GET /publishing/capabilities` | publishing token | limits and the token's subject |
| `GET /publishing/documents/{id}` | publishing token | current ETag of one explicitly named document |
| `POST /publishing/imports`, `GET /publishing/imports/{id}` | publishing token | publish, and see own imports; the same validation and commit as browser imports |
| `GET /publishing/results/{import_id}` | publishing token | readback of an own committed import |

Transport rules:

- Credentials are accepted only over HTTPS, or from loopback in local mode.
- Behind a gateway, `X-Forwarded-Proto: https` is trusted only from `GATEWAY_TRUSTED_PROXIES`.
- The ingress must let `/api/v1/publishing/*` through without browser sign-in (the
  application authenticates it), and must not strip the `Authorization` header. Templates
  are B13. Proving the whole sequence through a real ingress is A36, which is part of B13.

## Generator

```
bidoc connect https://docs.example.com         # prompts for the token (or --token-stdin)
bidoc publish out/Sales--1a2b3c4d.shared.html   # new document, or a new version against its ETag
bidoc disconnect
```

- The URL goes in `config.json`. The token goes in **Windows Credential Manager**; on other
  systems, a file in the generator home readable only by its owner. It is never written to
  documents, history or logs.
- Only `https://` URLs are accepted (plus `http://` to this computer). Redirects are never
  followed, so the token cannot be forwarded to another host.
- Each publication uses an Idempotency-Key made from the artifact digest and the
  precondition. A retry after a lost response returns the original outcome; connection
  errors are retried with the same key.
- Exit codes:
  - `0`: published;
  - `3`: not connected, or the token was rejected;
  - `4`: failed;
  - `5`: some files failed.
- **Desktop app:** the Library screen connects, shows whether the connection works
  (connected, token rejected, or unreachable) and disconnects. Completed shared documents
  get a **Publish** button.

## Installer downloads

- **Upload:** administrators add a release on **Download generator**. They upload the
  installer from the CI `windows-installer` artifact; the browser computes its SHA-256 and
  the library checks it against the bytes (`POST /releases`).
- **Approval:** a release is offered only after `POST /releases/{version}/approve`.
  Versions are immutable.
- **What readers see:** the approved version with its date, size, checksum, prerequisites,
  notes and an unsigned-build warning, or an honest "not available yet".
- **Downloads:** `GET /releases/{version}/download` serves only approved releases. It
  re-checks the checksum and sends the file as an attachment with an `X-Checksum-SHA256`
  header.

Storage:
- **Local:** `releases/{version}/{file}`, with rows in `releases`.
- **Azure:** blob `releases/{version}/{file}`, with rows in partition `releases`.

## Evidence

- `apps/library/tests/test_publishing.py` runs on LocalStore, in-memory Azure and Azurite.
  It covers:
  - the token shown once and stored hashed;
  - the cookie-free publish, retry, new-version and readback sequence;
  - bad, expired and revoked tokens failing alike (A11);
  - the token reaching nothing outside `/publishing/*`;
  - HTTPS enforcement and trusted-proxy forwarding;
  - owner and admin revocation and revoke-subject;
  - private imports;
  - release checksum, approval, immutability, download and roles.
- `apps/generator/tests/test_publish.py` runs against a real library server on loopback. It
  covers:
  - connect;
  - publish, retry and new version;
  - a revoked token giving exit 3;
  - a partial failure giving exit 5;
  - the desktop Library screen and Publish action;
  - HTTPS-only URLs and redirect refusal.
- `test_credentials.py` checks Windows Credential Manager on the `generator-windows` CI job,
  and the owner-only file elsewhere.
- Browser acceptance covers the unavailable state on Downloads, and a token shown once and
  then revoked.
