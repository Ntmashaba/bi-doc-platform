# bi-doc-platform

Generates readable documentation for Power BI and Azure Data Factory assets, and optionally publishes it to a shared library.
Everything is static analysis of files you give it. Nothing connects to a live server, and nothing refreshes data.

| You have | Kinds accepted |
|---|---|
| Power BI | `.pbix` (extracted first), Tabular backups (`.abf`), PBIP projects, TMDL, `model.bim`, PBIR, an extracted folder |
| Azure Data Factory | a Git folder, an ARM template, a resources export |

Two outputs are kept apart on purpose: a **local** document with everything the engines produce, and a **shared** document that
is projected for publication (machine paths removed, credentials and URL tokens cleaned on a best-effort basis, query code
withheld unless you opt in). See [ADR 0001](docs/decisions/0001-b01-owner-decisions.md).

## What is in the repository

| Path | What it is |
|---|---|
| [`components/power-bi`](components/power-bi), [`components/adf`](components/adf) | The two documentation engines (`pbidocgen`, `adfdocgen`). Their history and the commits they were imported at are in [`components/README.md`](components/README.md) |
| [`packages/contracts`](packages/contracts), [`packages/engines`](packages/engines), [`packages/relationships`](packages/relationships) | The envelope and scope contracts; the adapters, projection and identity code between the engines and the apps; and the detection of links between Data Factory activities and Power BI sources |
| [`apps/generator`](apps/generator) | `bidoc`: the command line, batches, local history and the desktop app |
| [`apps/library`](apps/library) | The shared library: HTTP API, web interface, local or Azure storage |
| [`packaging/windows`](packaging/windows) | The Windows installer (unsigned development build) and its acceptance script |
| [`deploy/azure`](deploy/azure), [`Dockerfile`](Dockerfile) | Docker image and Azure deployment template for the library |
| [`samples/manifest.json`](samples/manifest.json) | Checksum-pinned public sample files, downloaded on demand and never committed |
| [`docs/`](docs) | Design, operation and verification notes. Start with [`docs/PROGRESS.md`](docs/PROGRESS.md) |

## Quick start

Needs Python 3.11 or later and Git. Windows (PowerShell):

```powershell
git clone https://github.com/Ntmashaba/bi-doc-platform.git
cd bi-doc-platform
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt           # every package, in dependency order (editable)
python -m pip install -e "apps/generator[desktop]"  # only needed for the desktop window (adds pywebview)
bidoc doctor                                        # what this machine can generate, with fixes for what it cannot
```

macOS and Linux: the same, with `source .venv/bin/activate` instead of the `Activate.ps1` line.

### Generate documentation

One input at a time:

```powershell
bidoc generate --engine power_bi --kind pbix --source "C:\Reports\Sales.pbix" --output-dir "C:\Documentation"
bidoc generate --engine power_bi --kind pbip --source "C:\Projects\Sales\Sales.pbip" --output-dir "C:\Documentation"
bidoc generate --engine adf --kind adf_git --source "C:\Repos\factory" --output-dir "C:\Documentation"
```

Several at once, one after another, each succeeding or failing on its own (`bidoc batch` recognises each input by its shape):

```powershell
# folders are searched, subfolders included, for .pbix and .abf files, .bim models and project folders
bidoc batch ".\h3 Reports" ".\h4 Reports" --output-dir ".\Documentation"

# project folders (PBIP, TMDL, PBIR, pbi-tools extract, Data Factory Git folder) and single files work too
bidoc batch "C:\Projects\Sales" "C:\Repos\factory" "C:\Reports\Budget.pbix" --output-dir "C:\Documentation"
```

A folder that is itself a project counts as one input and is not searched inside. Other files (a stray `.json`, for example) are
ignored. The output folder, the generator's own folder, `.git` and `.venv` are left out, and links and junctions are never followed
(a selected link is reported as a failed item). A folder that
cannot be read, or that holds nothing usable, is reported as a failed item while every readable input still runs. The command
prints what it found before it starts, and each document is labelled with its path (`h3 Reports/Finance/Budget.pbix`) so two
files with the same name can be told apart.

Add `--profile shared` for the projected, publishable form. Every option: [`docs/generator.md`](docs/generator.md).

### Read the output

Each input becomes one self-contained HTML file in the output folder. **Open it in a browser**; it works offline. A name ending
in `.local.html` means the input was not read completely, so that document is for your own use and cannot be published (its
coverage section says why).

To browse many documents together, build the offline hub, a folder you can open from disk with the network off:

```powershell
bidoc export-library "C:\Documentation-Hub" "C:\Documentation"
```

Then **open `C:\Documentation-Hub\index.html`**: a catalogue with search, a viewer and links between related objects. It is a
snapshot; run the command again to refresh it. Documents without a publication manifest (the `.local.html` ones) are skipped
with a warning. Details: [`docs/portable-export.md`](docs/portable-export.md).

### Desktop app

```powershell
bidoc desktop              # opens the window (needs the desktop extra installed above)
bidoc desktop --no-window  # no window: serves the same app on loopback and prints the address to open in a browser
```

### Shared library

Windows (PowerShell):

```powershell
$env:LOCAL_DATA_DIR = ".\bidoc-data"
python -m bidoc_library          # http://127.0.0.1:8765
```

macOS and Linux: `LOCAL_DATA_DIR=./bidoc-data python -m bidoc_library`.
Docker: [`docs/deployment-docker.md`](docs/deployment-docker.md). Azure: [`docs/azure-deployment.md`](docs/azure-deployment.md).
API: [`docs/library-api.md`](docs/library-api.md).

## Reading a PBIX file

Two extractors, chosen the same way by every entry point (`bidoc generate`, `batch`, the desktop app, the worker and `doctor`):

- **pbi-tools Desktop** (Windows only, needs Power BI Desktop; AGPL-3.0, installed separately and never bundled). Record it with
  `bidoc config --pbi-tools C:\path\to\pbi-tools.exe`.
- **The portable reader** (pbixray, any operating system), used when you ask for it (`--backend pbixray`) or when no usable
  pbi-tools is available. ABF backups always use it.

An explicit choice is never overridden, and a failed extraction is never silently retried with the other extractor.
Details, limits and what a document can and cannot say: [`docs/portable-extraction.md`](docs/portable-extraction.md).

### Thin reports and models

A **thin report** (a live-connection report that holds no model of its own) is detected from the connection details it records.
Its document lists what the report needs (pages, visuals, field references) and says plainly that the fields are unresolved,
because the model is somewhere else. To resolve them, supply a model snapshot yourself and pair it with `--model`
(an ABF backup, `.bim` or TMDL):

```powershell
bidoc generate --engine power_bi --kind pbir --source "C:\Reports\Sales.Report" --model "C:\Models\Sales.abf" --output-dir "C:\Documentation"
```

Pairing is explicit and unverified: the document states that the snapshot's server identity and freshness are not checked. A
report that already has its own model is not given a different one.

## Windows

[`packaging/windows`](packaging/windows) builds an unsigned per-user installer. The hosted CI installs, uses, upgrades and
uninstalls it, but a hosted runner is not a clean machine. The steps for the checks that need your own Windows machine are in
[`docs/validation/windows-validation-pack.md`](docs/validation/windows-validation-pack.md); the first real run (2026-10-01) is kept
as a baseline in [`docs/validation/results/`](docs/validation/results).

## Tests

```
python scripts/test.py               # all seven unit suites
python scripts/test.py --samples     # also the public sample acceptance run (downloads checksum-pinned files)
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) also runs the browser checks, the Windows jobs, Docker, Azurite and
the sample documents.

## Status and limits

Progress and open items: [`docs/PROGRESS.md`](docs/PROGRESS.md).

- **Live Analysis Services (SSAS) scanning is not implemented.** Nothing connects to a server, workspace or service. A remote
  model is documented only from a model file you supply.
- **Verified:** real pbi-tools extraction was run on one Windows machine, on five public files, and compared with the portable
  reader there.
- **Not verified:** a clean-machine Windows installation; row-level-security parity between the two extractors (all five test
  files had no roles); a genuine matched thin-report and Tabular-backup pair (none is public, so the repository's pairing
  demonstration is synthetic and labelled so).

## Provenance and maintenance

The two engines began as the standalone repositories [`Ntmashaba/pbi-doc-gen`](https://github.com/Ntmashaba/pbi-doc-gen) and
[`Ntmashaba/adf-doc-gen`](https://github.com/Ntmashaba/adf-doc-gen). Their code was imported here at fixed commits
([`components/README.md`](components/README.md)) and is maintained here from then on; new work belongs in this repository. The
standalone repositories stay public and unarchived: they carry a pointer to this repository, and four public sample `.pbix` files
that [`samples/manifest.json`](samples/manifest.json) downloads for tests (pinned by commit and checksum) are still served from
`pbi-doc-gen`.

No licence file has been added to this repository yet.
