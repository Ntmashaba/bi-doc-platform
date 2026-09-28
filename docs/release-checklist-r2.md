# R2 release checklist (B13)

R2 is the Windows generator and installer, portable offline export, the Azure deployment
profile and direct publishing (handoff section 1). A release is ready when every line
below is ticked with evidence.

**Who runs each line:**

- **CI:** a job in `.github/workflows/ci.yml` must be green on the release commit.
- **Owner:** needs the client's machines, tenant or signing certificate. Record the date,
  who ran it and the result.

## Build and supply chain

| | Check | Who | Evidence |
|---|---|---|---|
| ☐ | All CI jobs green on the release commit | CI | the run URL |
| ☐ | Engines pinned to merged commits (`packages/engines/pyproject.toml`); lock files current | CI (`tests`, `docker`) | |
| ☐ | pbi-tools is not bundled; the installer checks for it as a prerequisite (AGPL-3.0, ADR 0001) | CI (`windows-installer`) | |
| ☐ | Installer built with version metadata, `SHA256SUMS.txt` and release notes | CI (`windows-installer`) | artifact |
| ☐ | Installer **code-signed** with the client's certificate (development builds are labelled unsigned) | Owner | signing log |
| ☐ | Library image built from the lock file, non-root; pushed with its version tag | CI (`docker`) plus Owner push | image digest |

## Windows generator (A08, A13)

| | Check | Who | Evidence |
|---|---|---|---|
| ☐ | W1: packaged generator builds and runs on Windows | CI (`windows-installer`, `generator-windows`) | |
| ☐ | W2: real PBIX extraction with Power BI Desktop and pbi-tools on the owner's machine | Owner | `docs/b09/VERIFICATION.md` |
| ☐ | A13 on a clean Windows machine: install, generate, upgrade, uninstall keeps documents | Owner | |
| ☐ | Offline generation and viewing with the network disconnected | Owner | |

## Portable export (A12, A38)

| | Check | Who | Evidence |
|---|---|---|---|
| ☐ | Complete and partial exports open from `file://` in Chromium and Microsoft Edge, with zero network requests, Back support and "Not included" links | CI (`frontend`, `generator-windows`) | |
| ☐ | An export from the client's own documents opens on a machine with no network | Owner | |

## Azure deployment (B13)

| | Check | Who | Evidence |
|---|---|---|---|
| ☐ | Template compiles and lints clean, keeps the pilot limits, and sets only valid configuration | CI (`azure-template`) | |
| ☐ | Store, API, relationships, backup and publishing suites pass against Azurite | CI (`azurite`) | |
| ☐ | A15: the same suites against a disposable real storage account | Owner (needs authorization) | |
| ☐ | Deployed with `docs/azure-deployment.md`; sign-in works for each role; unassigned users are refused | Owner | step 6.1 |
| ☐ | The platform principal is trusted only from the platform, and a forged header is ignored | Owner | steps 6.2, 6.3 |
| ☐ | Budget alert emails reach the operations mailbox | Owner | |
| ☐ | Cost worksheet filled in for the client's region | Owner | |

## Direct publishing (A36)

| | Check | Who | Evidence |
|---|---|---|---|
| ☐ | Cookie-free publish, new version, retry and readback through a TLS ingress; escalation and revocation fail | CI (`docker`, `check_ingress.py`) | |
| ☐ | The same sequence through the client's real ingress | Owner | step 6.4 |
| ☐ | Removing a publisher: role removed, then tokens revoked with revoke-subject | Owner | |

## Data safety (A14, A28)

| | Check | Who | Evidence |
|---|---|---|---|
| ☐ | Backup and restore into a clean deployment keep IDs, revisions, checksums, generations, manual assertions and audit | CI (`tests`, `azurite`) | |
| ☐ | First production backup taken, verified and restored into a scratch deployment | Owner | `docs/backup-restore.md` |
| ☐ | Backup schedule and retention agreed and stored outside the library's account | Owner | |

## Scale (A41)

| | Check | Who | Evidence |
|---|---|---|---|
| ☐ | Large-document import, memory, rebuild and search within budget; server search equals browser search | CI (`large-documents`) | |
| ☐ | Upload limits and container size agreed for the client's largest model (`docs/performance.md`) | Owner | |

## Sign-off

| Role | Name | Date |
|---|---|---|
| Owner | | |
| Client operations | | |
