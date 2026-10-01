# bi-doc-platform

Generates readable documentation for Power BI and Azure Data Factory assets, and optionally publishes it to a shared library.
Everything is static analysis of files; nothing connects to a live server or refreshes data.

| You have | Kinds accepted |
|---|---|
| Power BI | `.pbix` (extracted first), Tabular backups (`.abf`), PBIP projects, TMDL, `model.bim`, PBIR, an extracted folder |
| Azure Data Factory | a Git folder, an ARM template, a resources export |

Two outputs are kept apart on purpose: a **local** document with everything the engines produce, and a **shared** document that
is projected for publication (machine paths removed, credentials and URL tokens cleaned on a best-effort basis, query code
withheld unless you opt in). See `docs/decisions/0001-b01-owner-decisions.md`.

## What is in the repository

| Path | What it is |
|---|---|
| `components/power-bi`, `components/adf` | The two documentation engines (`pbidocgen`, `adfdocgen`), imported from the earlier `Ntmashaba/pbi-doc-gen` and `Ntmashaba/adf-doc-gen` repositories, which are now frozen (`components/README.md`) |
| `packages/contracts`, `packages/engines`, `packages/relationships` | The envelope and scope contracts; the adapters, projection and identity code between the engines and the apps; and the detection of links between Data Factory activities and Power BI sources |
| `apps/generator` | `bidoc`: the command line, batches, local history and the desktop app |
| `apps/library` | The shared library: HTTP API, web interface, local or Azure storage |
| `packaging/windows` | The Windows installer (unsigned development build) and its acceptance script |
| `deploy/azure`, `Dockerfile` | Docker image and Azure deployment template for the library |
| `samples/manifest.json` | Checksum-pinned public sample files, downloaded on demand, never committed |
| `docs/` | Design, operation and verification notes. Start with `docs/PROGRESS.md` |

## Quick start

Python 3.11 or later.

```
pip install -r requirements.txt     # development install of every package, in dependency order
bidoc doctor                        # what this machine can generate, with fixes for what it cannot

bidoc generate --engine power_bi --kind pbip --source Sales.pbip --output-dir docs
bidoc generate --engine power_bi --kind pbix --source Sales.pbix --output-dir docs
bidoc generate --engine adf --kind adf_git --source path/to/factory --output-dir docs
bidoc batch Finance/*.pbix Sales-project/ adf-repo/ --output-dir docs
bidoc desktop                       # the desktop app
```

Add `--profile shared` for the projected, publishable form. All options: `docs/generator.md`.

**Run the shared library locally:**

```
LOCAL_DATA_DIR=./bidoc-data python -m bidoc_library     # http://127.0.0.1:8765
```

Docker: `docs/deployment-docker.md`. Azure: `docs/azure-deployment.md`. API: `docs/library-api.md`.

## Reading a PBIX file

Two extractors, chosen the same way by every entry point (`bidoc generate`, `batch`, the desktop app, the worker and `doctor`):

- **pbi-tools Desktop** (Windows only, needs Power BI Desktop; AGPL-3.0, installed separately and never bundled). Record it with
  `bidoc config --pbi-tools C:\path\to\pbi-tools.exe`.
- **The portable reader** (pbixray, any operating system), used when you ask for it (`--backend pbixray`) or when no usable
  pbi-tools is available. ABF backups always use it.

An explicit choice is never overridden, and a failed extraction is never silently retried with the other extractor.
Details, limits and what a document can and cannot say: `docs/portable-extraction.md`.

## Windows

`packaging/windows` builds an unsigned per-user installer. The hosted CI installs, uses, upgrades and uninstalls it, but a
hosted runner is not a clean machine. The steps for the checks that need your own Windows machine are in
`docs/validation/windows-validation-pack.md`; the first real run (2026-10-01) is kept as a baseline in
`docs/validation/results/`.

## Tests

```
python scripts/test.py               # all seven unit suites
python scripts/test.py --samples     # also the public sample acceptance run (downloads checksum-pinned files)
```

CI (`.github/workflows/ci.yml`) also runs the browser checks, the Windows jobs, Docker, Azurite and the sample documents.

## Status and limits

Progress and open items are in `docs/PROGRESS.md`. Honest summary: real pbi-tools extraction has been run on one Windows
machine with five public files and compared with the portable reader there; a clean-machine installation, live Analysis
Services scanning, row-level-security parity and a genuine matched thin-report/ABF pair have not been verified.

No licence file has been added to the repository yet.
