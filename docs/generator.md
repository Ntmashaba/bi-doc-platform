# Generator (`bidoc`)

The command line (B03) plus, from B08, PBIX extraction, batches, local history and the
desktop app. Direct publishing (B11) comes later.

```
pip install -r requirements.txt          # contracts, engines (components/), generator
bidoc doctor                             # what this machine can generate, with fixes
bidoc generate --engine adf --kind adf_git --source path/to/factory --output-dir docs
bidoc generate --engine power_bi --kind pbip --source Sales.pbip --output-dir docs --profile shared
bidoc generate --engine power_bi --kind pbix --source Sales.pbix --output-dir docs    # pbi-tools if configured, else portable
bidoc batch "h3 Reports" Sales-project/ adf-repo/ --output-dir docs                   # folders are searched; one at a time
bidoc history                                                                          # recent batches
bidoc retry ITEM_ID                                                                    # failed, cancelled or interrupted
bidoc desktop                                                                          # the desktop app
```

| Option | Meaning |
|---|---|
| `--engine` | `power_bi` or `adf` |
| `--kind` | Power BI: `pbix` (extracted first: pbi-tools if configured, otherwise the portable reader, see `portable-extraction.md`), `abf` (Tabular backup), `pbip`, `tmdl`, `bim`, `pbir`, `extracted` (pbi-tools extract folder). ADF: `adf_git`, `adf_arm`, `adf_resources` |
| `--profile local` (default) | Everything the engines produce, query code included, for your own use |
| `--profile shared` | Projected for the shared library: machine paths removed, credentials, URL tokens and entered data cleaned (best effort), query code withheld |
| `--include-query-code` | Shared profile only: publish M/SQL as written. The cleaning above still runs, but it is not a guarantee |
| `--environment` | e.g. `Production`; part of the document identity (different environments are different documents) |
| `--identity existing\|new` | Required when a source was copied or moved and carries another source's identity |
| `--document-id` | Explicitly reuse a known document identity |
| `--title`, `--description`, `--tag`, `--business-area`, `--owner` | Catalogue metadata |
| `--json` | Machine-readable result |

Exit codes: `0` success, `2` invalid input or configuration, `3` prerequisites missing, `4` generation failure (a batch where every item failed), `5` partial batch failure, `130` cancelled.

Output is `<title>--<document id prefix>.html` (`.shared.html` for the shared profile), written atomically so a previous good file is only replaced by a complete one. If the input was not read completely (for example ADF files that could not be parsed), the document is still written as `<title>.local.html`, without a publication manifest, and cannot be published.

## PBIX extraction

PBIX files are read by pbi-tools Desktop, installed separately (AGPL-3.0; see ADR 0001), with
Power BI Desktop, on Windows, or by the portable reader on any OS (an approximation; see
`portable-extraction.md` for what it covers and where it is incomplete). One policy chooses between
them for `generate`, `batch`, the desktop app and the worker: a pbi-tools that can run here is used,
the portable reader otherwise, and an explicit `--pbi-tools` or `--backend` is never overridden.
`bidoc doctor` reports the choice, whether PBIX generation is ready and why not. PBIP, model, report
and Data Factory inputs never depend on it (A07).

- The tool path is local configuration (`--pbi-tools`, `BIDOC_PBI_TOOLS`, or `PATH`). Script
  wrappers and pbi-tools.core are refused.
- pbi-tools runs as a child process with an argument array, never a shell, in the item's
  own workspace under the generator home (`workspaces/<item>`).
- A timeout (`--extract-timeout`, default 900 s) or a cancellation ends the whole process
  tree: a process group on POSIX, `taskkill /T /F` on Windows.
- Successful items delete their workspace. Failed ones keep it, with `pbi-tools.log`, for 7
  days.
- The document's identity, source label and hash come from the PBIX. Its content comes from the
  extract plus the PBIX itself (PBIR report definitions and custom visual names are read from
  it as a zip).
- Extraction never refreshes source data.

## Batches and history

`bidoc batch` and the desktop app recognise inputs by shape:

- `.pbix` files;
- PBIP project folders, or a `.pbip` file whose `.SemanticModel` folder sits beside it (a lone
  pointer is refused);
- TMDL and PBIR folders, and `model.bim`;
- pbi-tools extracts;
- Data Factory Git folders, and ARM or resource JSON.

**Any other folder is searched** (`bidoc_generator/discovery.py`, used by the command line, the desktop review and the desktop
submission alike), subfolders included, for `.pbix` and `.abf` files, `.bim` models and project folders:

- a recognised project folder (PBIP, TMDL, PBIR, pbi-tools extract, Data Factory Git folder) is one input and is not searched
  inside, so a model's own files or a factory's individual JSON files are never queued separately. A Data Factory Git folder
  needs JSON in at least one of its `pipeline`, `dataset`, `linkedService`, `dataflow`, `trigger` or `factory` folders, so a
  reports folder that merely has a subfolder with one of those names is still a container;
- other files are ignored, including stray `.json` (only an explicitly selected JSON file is treated as a Data Factory export);
- the output folder, the generator's own folder, `.git`, `.venv`, `venv`, `node_modules` and similar, and any symbolic link or
  Windows junction are skipped (links are reported, not followed). A scan stops, with a message, after 1000 inputs or 20000
  folders;
- a folder that cannot be read, an empty scan and a scan that hit a bound are *failed items* with a message, so they appear in
  the history and the exit code while every readable input still runs. Retrying one searches that folder again; if it now holds
  several inputs the retry says so, and the folder should be queued again as a new batch;
- selections keep the order given; a folder's finds are sorted by path; the same file reached twice is queued once; documents
  are labelled with the path below the selected folder's parent. Several inputs with the same file name get a note, because a
  document that cannot be published is named after the file and one may overwrite another.

`bidoc batch` prints what it found before it starts. The desktop review lists the same inputs and **Generate queues exactly that
reviewed list** (a snapshot: files added after the review are not included; review again to pick them up). The offline hub is a
separate step: `bidoc export-library`.

Items run one at a time, so there is only ever one PBIX extraction. Each item moves through
`queued → validating → (extracting) → analysing → rendering → completed | failed | cancelled`.
One item's failure never affects the others (A08). Failed, cancelled and interrupted items can
be retried on their own.

History is kept in `history.sqlite3` in the generator home: `BIDOC_HOME`, otherwise
`%LOCALAPPDATA%\bidoc` on Windows or `~/.local/share/bidoc` elsewhere. Desktop processing is
not a background service. If the app closes while an item runs, that item is marked
`interrupted` at the next launch and can be retried; nothing claims it carried on.

## Desktop app

`bidoc desktop` opens a pywebview window (`pip install bi-doc-generator[desktop]`).
`--no-window` serves the same app on loopback for a browser. The screens are:

- a new batch with native file and folder pickers;
- prerequisites per input type;
- review of the inputs before anything runs;
- live per-item progress with elapsed time, warnings, errors, Open, Cancel and Retry;
- history, including items interrupted at the last close.

Closing with work in progress asks whether to cancel it or keep working.

Security follows the library's local mode:

- the app listens on 127.0.0.1 only; the Host header must name it, and a foreign Origin is
  refused;
- every change needs the per-launch session secret and the anti-CSRF header;
- generated documents are served only by item ID, under `sandbox allow-scripts` with no
  network;
- in the desktop window, previews open in a separate window with no host bridge.

The existing `pbi-doc-gen` and `adf-doc-gen` command lines are unchanged.

## Portable offline export

`bidoc export-library OUTPUT INPUT...` writes a folder that works without a network or a
library server, opened straight from disk. See `docs/portable-export.md`.

## Worker mode (optional R3)

`bidoc worker connect URL`, then `bidoc worker run` processes jobs that people upload to a
library, one at a time, with the same engines. See `docs/workers.md`.
