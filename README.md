# bi-doc-platform

**Turn Power BI and Azure Data Factory files into searchable, offline documentation.** Generate one HTML document per
report, model or factory; browse many documents together in an offline catalogue; or publish them to a shared library.

The platform **analyses files you provide**. It does not connect to Power BI, Analysis Services, Azure Data Factory or
source databases, and it does not refresh data.

> **New here?** Start with [Generate your first document](#generate-your-first-document). **Looking for something
> specific?** Jump to [Find a feature](#find-a-feature) or the [Command cheat sheet](#command-cheat-sheet).

## On this page

- [What can it document?](#what-can-it-document)
- [Generate your first document](#generate-your-first-document)
- [Find a feature](#find-a-feature)
- [Command cheat sheet](#command-cheat-sheet)
- [Understand the generated HTML](#understand-the-generated-html)
- [Local and shared documents](#local-and-shared-documents)
- [PBIX extraction and troubleshooting](#pbix-extraction-and-troubleshooting)
- [Documentation guides](#documentation-guides)
- [For contributors](#for-contributors)
- [Current limitations](#current-limitations)

## What can it document?

| Source | Supported inputs | Typical questions |
|---|---|---|
| **Power BI** | `.pbix`, Tabular backups (`.abf`), PBIP projects, TMDL, `model.bim`, an SSMS CREATE script of a tabular database (`.xmla`), PBIR, pbi-tools extracted folders | Where does the data come from? Which measures and columns are used, and on which pages? What depends on this field? What could be removed? |
| **Azure Data Factory** | a Git folder, an ARM template export, resource JSON (one file, or a folder of files saved from ADF Studio's "view code") | What starts a pipeline? Which datasets and linked services does it use? What reads or writes a source? What breaks downstream? |

A `.pbix` or `.abf` is extracted first; see [PBIX extraction](#pbix-extraction-and-troubleshooting). A `.xmla` file must be a
CREATE script of a tabular database (JSON at compatibility level 1200 and later, XML at 1100 and 1103), not any XMLA command.
Exact input rules: [generator guide](docs/generator.md).

## Generate your first document

### 1. Install

**Requirements:** Python 3.11 or later, and Git. On Windows, in PowerShell:

```powershell
git clone https://github.com/Ntmashaba/bi-doc-platform.git
cd bi-doc-platform
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
bidoc doctor
```

On macOS or Linux, use `source .venv/bin/activate` instead of the `Activate.ps1` line. For the desktop window, also run
`python -m pip install -e "apps/generator[desktop]"`.

`bidoc doctor` says which inputs this machine can document and how to fix what it cannot. PBIP projects, models and Data
Factory folders never need pbi-tools or Power BI Desktop.

### 2. Generate a document

Pick **one** example that matches your source, with your own paths:

```powershell
# Power BI project
bidoc generate --engine power_bi --kind pbip --source "C:\Projects\Sales\Sales.pbip" --output-dir ".\Documentation"

# Power BI PBIX
bidoc generate --engine power_bi --kind pbix --source "C:\Reports\Sales.pbix" --output-dir ".\Documentation"

# Azure Data Factory Git folder
bidoc generate --engine adf --kind adf_git --source "C:\Repos\factory" --output-dir ".\Documentation"
```

The command prints the path of the HTML it wrote.

### 3. Open it

Open the `.html` file in a browser. It is self-contained and works offline. Start at **Overview**, then use the sidebar;
[Understand the generated HTML](#understand-the-generated-html) lists the sections.

A file ending in `.local.html` means the input was not read completely: the document's coverage section says why, and it
cannot be published.

### 4. Optional: a whole folder

```powershell
bidoc batch "C:\Reports" --output-dir ".\Documentation"
```

`bidoc batch` searches folders (subfolders included) for supported files and project folders, documents each on its own, and
records failures without stopping the rest. A project folder counts as one input; links and junctions are never followed.
Retry a failed item with `bidoc retry ITEM_ID` (`bidoc history` lists them). [Batches and history](docs/generator.md#batches-and-history).

### 5. Optional: browse many documents in one catalogue

```powershell
bidoc export-library ".\Documentation-Hub" ".\Documentation"
```

Open `Documentation-Hub\index.html`: a catalogue with search, a viewer and links between related Power BI and Data Factory
objects. It is a snapshot; run the command again after regenerating. [Offline export](docs/portable-export.md).

## Find a feature

| I want to… | Start here |
|---|---|
| Generate one Power BI or Data Factory document | [First document](#generate-your-first-document) · [Generator options](docs/generator.md) |
| Document a folder, and retry what failed | [Batches and history](docs/generator.md#batches-and-history) |
| See where Power BI data comes from | [Power BI sources](components/power-bi/README.md#sources) · [Power Query](components/power-bi/README.md#power-query) |
| See which measures and columns each report page uses | [What is in a report page](components/power-bi/README.md#what-is-in-a-report-page) |
| Find what breaks if a field changes | [Power BI impact](components/power-bi/README.md#4-what-breaks-if-i-change-this) · [Data Factory views](components/adf/README.md#the-views) |
| Review columns and measures that look unused | [Power BI cleanup review](components/power-bi/README.md#cleanup-which-columns-and-measures-can-be-removed) |
| Follow Data Factory triggers, pipelines and lineage | [Data Factory views](components/adf/README.md#the-views) |
| Understand warnings, missing references or incomplete analysis | [Power BI warnings](components/power-bi/README.md#warnings) · [Data Factory caveats](components/adf/README.md#reading-the-verdicts-caveats-that-matter) |
| Compare two versions of a Power BI document | [Word, JSON, CSV and agent outputs](docs/generator.md#other-outputs-beside-the-document---also) (`--also json` writes the extract **Compare extracts** reads) |
| Get Word, JSON, CSV or an agent context file | [`--also`](docs/generator.md#other-outputs-beside-the-document---also) |
| Record a report's location, owner or support details | [Details the source cannot give](docs/generator.md#details-the-source-cannot-give) |
| Pair a live-connected report with a model snapshot | [Thin reports and models](#thin-reports-and-models) |
| Open many reports and factories together offline | [Offline catalogue](docs/portable-export.md) |
| Share documents through the library | [Publishing](docs/publishing.md) · [Library interface](docs/library-ui.md) |
| Fix a PBIX that will not extract | [PBIX extraction](#pbix-extraction-and-troubleshooting) · [When pbi-tools fails](docs/generator.md#when-pbi-tools-fails-on-a-file) |
| Use the desktop app | [Desktop app](docs/generator.md#desktop-app) |
| Run or deploy the shared library | [Run it locally](#run-the-shared-library-locally) · [Docker](docs/deployment-docker.md) · [Azure](docs/azure-deployment.md) |
| Know what the tool cannot tell | [Limitations](#current-limitations) · [Portable reader coverage](docs/portable-extraction.md) |

**Which command?** `bidoc` is the platform command: it generates, batches, exports and publishes, and its documents carry the
integrity check the library relies on. Each engine also has its own `python generate_docs.py`, described in the
[Power BI engine](components/power-bi/README.md) and [Data Factory engine](components/adf/README.md) guides, with its own
options and outputs. Do not assume a flag from one works on the other.

## Command cheat sheet

From the activated environment; replace the example paths.

| Task | Command |
|---|---|
| Check prerequisites | `bidoc doctor` |
| Generate one document | `bidoc generate --engine power_bi --kind pbip --source "Sales.pbip" --output-dir docs` |
| Generate a folder | `bidoc batch "C:\Reports" --output-dir docs` |
| Make a shareable document | add `--profile shared` |
| Also write JSON, Word, CSV or agent context | add `--also json,word,csv,agent` |
| Record details the source cannot give | keep `<source name>.bidoc-details.json` beside the source (every command reads it), or add `--details details.json` to `bidoc generate` |
| Choose the PBIX extractor | add `--backend pbixray` or `--backend pbi-tools` (on `generate` and `batch`) |
| Record where pbi-tools is | `bidoc config --pbi-tools "C:\Tools\pbi-tools.exe"` |
| Open the desktop app | `bidoc desktop` |
| Use the desktop app in a browser | `bidoc desktop --no-window` |
| See recent batches | `bidoc history` |
| Retry a failed item | `bidoc retry ITEM_ID` |
| Build the offline catalogue | `bidoc export-library ".\Hub" ".\Documentation"` |
| Connect to a shared library | `bidoc connect https://docs.example.com` |
| Publish a shared document | `bidoc publish ".\Documentation\Sales--1a2b3c4d.shared.html"` |
| Disconnect | `bidoc disconnect` |

Publishing needs a running library and a publishing token created in it; see [Publishing tokens](docs/publishing.md#publishing-tokens)
and [Publishing from the generator](docs/publishing.md#generator). Every option: [generator guide](docs/generator.md) and
`bidoc <command> --help`.

### Run the shared library locally

```powershell
$env:LOCAL_DATA_DIR = ".\bidoc-data"
python -m bidoc_library          # http://127.0.0.1:8765
```

On macOS or Linux: `LOCAL_DATA_DIR=./bidoc-data python -m bidoc_library`. API: [library API](docs/library-api.md).

## Understand the generated HTML

A document is an **offline snapshot of the definitions you supplied**, not a live dashboard. Its sidebar follows the tool it
documents:

- **Power BI:** Overview, Data Sources, Power Query, Table view, Model view (relationships, security, lineage and usage), DAX
  query view (measures and calculations) and Report view (pages, visuals, filters and bookmarks). Below them, **Compare
  extracts** and **Documentation details**. [Power BI engine guide](components/power-bi/README.md).
- **Data Factory:** Overview, Data & sources, Pipelines & orchestration, Impact & dependencies, Review issues and **Factory
  details**. [Data Factory engine guide](components/adf/README.md#the-views).

Which views have content depends on the input: a report without a model, for example, has nothing in Table view.

- **Review findings are evidence, not instructions.** A column that looks unused may be used by a report this document does
  not include.
- **Documentation details and Factory details** (report location, owner, runbook and the like) are **read-only** in a
  document made by `bidoc`: it carries an integrity check, and a copy saved from the page would fail it. Record them at
  generation with `--details` or a `<source name>.bidoc-details.json` file beside the source;
  [details the source cannot give](docs/generator.md#details-the-source-cannot-give). A document made by an engine's own
  `generate_docs.py` has no integrity check and keeps its in-page editor.

## Local and shared documents

| Profile | For | What it keeps |
|---|---|---|
| `local` (default) | your own use | everything the engines produce, query code included |
| `shared` | publishing to the library | personal machine paths removed; credentials and URL tokens cleaned on a best-effort basis; query code withheld unless `--include-query-code` |

Extra outputs from `--also` follow their document's profile. A document made by `bidoc` is protected by an integrity hash:
editing its HTML breaks it, so regenerate instead. Shared projection is **not a guarantee** that everything sensitive is gone;
review a shared document before publishing it. [ADR 0001](docs/decisions/0001-b01-owner-decisions.md).

## PBIX extraction and troubleshooting

Two extractors, chosen the same way by `bidoc generate`, `batch`, the desktop app, the worker and `doctor`:

- **pbi-tools Desktop:** Windows only, needs Power BI Desktop, installed separately (AGPL-3.0, never bundled). Record it with
  `bidoc config --pbi-tools C:\path\to\pbi-tools.exe`.
- **Portable reader (pbixray):** any operating system. Used when you ask for it (`--backend pbixray`) or when no usable
  pbi-tools is available. ABF backups always use it.

An explicit choice is never overridden, and a failed extraction is never retried with the other extractor. If pbi-tools starts
but fails on a file, see [when pbi-tools fails](docs/generator.md#when-pbi-tools-fails-on-a-file). Coverage and limits:
[portable extraction](docs/portable-extraction.md).

### Thin reports and models

A **thin report** connects live to a model elsewhere, so its document lists the fields it uses as unresolved. To resolve them
against a model snapshot you supply (an ABF backup, a PBIX, a `.bim` or a TMDL folder), pair it:

```powershell
bidoc generate --engine power_bi --kind pbir --source "C:\Reports\Sales.Report" --model "C:\Models\Sales.abf" --output-dir ".\Documentation"
```

For `bidoc batch` and the desktop app, name each report's model in its own details file, for example
`Sales.Report.bidoc-details.json` containing `{"model": {"path": "../Models/Sales.abf"}}`.
[Pairing rules](docs/generator.md#pairing-a-live-connected-report-with-its-model).

The pairing is checked before anything runs, but **not verified**: the document says the model's server identity and how current
it is were not checked. A report that has its own model is never given another.

## Documentation guides

**Using the platform**

- [Generator: commands, batches, desktop app](docs/generator.md)
- [Power BI features, and its standalone engine](components/power-bi/README.md)
- [Data Factory features, and its standalone engine](components/adf/README.md)
- [Offline catalogue](docs/portable-export.md)
- [Publishing and tokens](docs/publishing.md)
- [Shared library interface](docs/library-ui.md) · [Library API](docs/library-api.md)

**Operations and troubleshooting**

- [PBIX extraction coverage](docs/portable-extraction.md)
- [Docker deployment](docs/deployment-docker.md) · [Azure deployment](docs/azure-deployment.md)
- [Worker mode](docs/workers.md)
- [Windows installer validation](docs/validation/windows-validation-pack.md)

## For contributors

| Location | What it is |
|---|---|
| [`components/power-bi`](components/power-bi) · [`components/adf`](components/adf) | The documentation engines (`pbidocgen`, `adfdocgen`) |
| [`packages/contracts`](packages/contracts) | Manifest (envelope) and validation contracts |
| [`packages/engines`](packages/engines) · [`packages/relationships`](packages/relationships) | Engine adapters, projection and identity; links between Data Factory activities and Power BI sources |
| [`apps/generator`](apps/generator) | `bidoc`: command line, batches, history, desktop app |
| [`apps/library`](apps/library) | Shared library: API, web interface, local or Azure storage |
| [`packaging/windows`](packaging/windows) · [`deploy/azure`](deploy/azure) · [`Dockerfile`](Dockerfile) | Windows installer (unsigned development build), Azure template, Docker image |
| [`samples/manifest.json`](samples/manifest.json) | Checksum-pinned public sample files, downloaded on demand |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | Implementation history and open engineering work (not a user guide) |

```powershell
python scripts/test.py              # every unit suite
python scripts/test.py --samples    # also the public sample acceptance run (downloads checksum-pinned files)
python scripts/check_links.py       # links and anchors between the Markdown files, offline
```

CI also runs browser, Windows, Docker, Azurite and sample-document checks;
[the workflow](.github/workflows/ci.yml) is the authoritative list.

## Current limitations

- This is **static analysis**: no server, workspace, factory or database is queried, and Analysis Services is not scanned. A
  remote model is documented only from a model file you supply.
- The extractors differ in coverage; read a document's warnings and coverage before acting on its findings.
- Shared projection cleans on a best-effort basis; it does not replace reading a document before publishing it.
- **Verified:** real pbi-tools extraction on one Windows machine, on five public files, compared with the portable reader.
- **Not yet verified:** a clean-machine Windows installation; row-level security parity between the extractors (none of the
  five files had roles); a genuine matched thin report and Tabular backup (none is public, so the pairing demonstration is
  synthetic and labelled so). Progress: [`docs/PROGRESS.md`](docs/PROGRESS.md).

## Project provenance

The engines began as [pbi-doc-gen](https://github.com/Ntmashaba/pbi-doc-gen) and
[adf-doc-gen](https://github.com/Ntmashaba/adf-doc-gen), were imported here at fixed commits
([`components/README.md`](components/README.md)) and are maintained here. The standalone repositories stay public; four
public sample `.pbix` files used by the tests are still served from pbi-doc-gen.

No licence file has been added to this repository yet; review third-party licence obligations before redistributing.
