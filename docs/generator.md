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
| `--kind` | Power BI: `pbix` (extracted first: pbi-tools if configured, otherwise the portable reader, see `portable-extraction.md`), `abf` (Tabular backup), `pbip`, `tmdl`, `bim` (a `model.bim`, or an SSMS CREATE script of a tabular database saved as `.xmla`; JSON at compatibility level 1200 and later, XML at 1100 and 1103), `pbir`, `extracted` (pbi-tools extract folder). ADF: `adf_git`, `adf_arm`, `adf_resources` |
| `--profile local` (default) | Everything the engines produce, query code included, for your own use |
| `--profile shared` | Projected for the shared library: machine paths removed, credentials, URL tokens and entered data cleaned (best effort), query code withheld |
| `--include-query-code` | Shared profile only: publish M/SQL as written. The cleaning above still runs, but it is not a guarantee |
| `--environment` | e.g. `Production`; part of the document identity (different environments are different documents) |
| `--identity existing\|new` | Required when a source was copied or moved and carries another source's identity |
| `--document-id` | Explicitly reuse a known document identity |
| `--title`, `--description`, `--tag`, `--business-area`, `--owner` | Catalogue metadata |
| `--details FILE` | Details the source cannot give, shown in the document (see below) |
| `--also json,word,csv,agent` | Other outputs beside the document (see below); also on `bidoc batch` |
| `--json` | Machine-readable result |

Exit codes: `0` success, `2` invalid input or configuration, `3` prerequisites missing, `4` generation failure (a batch where every item failed), `5` partial batch failure, `130` cancelled.

Output is `<title>--<document id prefix>.html` (`.shared.html` for the shared profile), written atomically so a previous good file is only replaced by a complete one. If the input was not read completely (for example ADF files that could not be parsed), the document is still written as `<title>.local.html`, without a publication manifest, and cannot be published.

## Other outputs beside the document (`--also`)

`bidoc generate` and `bidoc batch` write the HTML document. `--also` adds the engines' other outputs beside it,
named after it:

| Output | File | What it is |
|---|---|---|
| `json` | `<document>.json` | The extract: everything the document shows, as data. A Power BI document's **Compare extracts** takes an earlier one as its baseline |
| `word` | `<document>.docx` | The narrative subset, for handovers and sign-off |
| `csv` | Power BI: `<document>.columns.csv`; Data Factory: `<document>-csv/` with `objects.csv`, `usage.csv`, `edges.csv`, `issues.csv` | Column usage per report page (needs a model); the factory's objects, usage, lineage edges and review issues |
| `agent` | `<document>.agent.md` | Compact Markdown for an LLM agent's context |

```powershell
bidoc generate --engine power_bi --kind pbip --source "C:\Projects\Sales\Sales.pbip" --output-dir "C:\Documentation" --also json,word,agent
bidoc batch "C:\Reports" --output-dir "C:\Documentation" --also agent
```

- Every output is made from the content the document was rendered from. For `--profile shared` that is the content
  as filtered for sharing: what the document withholds (query code unless `--include-query-code`, credentials,
  personal locations, entered data), its extras withhold too.
- The extras are files for your own use beside the document. Only the HTML document carries the integrity
  check and can be published to the library.
- A failed extra is a warning naming the output and the kind of error (for a system error, its description, such as
  "No space left on device"); the document and the other extras are still written. The warning never repeats the
  error's own text, which could quote a value or a local path. An unknown output name is refused before anything is
  read.
- Each extra is written in a temporary folder beside the document and moved into place only once it is complete. A
  half-written file never replaces a good one. The four Data Factory CSVs are replaced as a folder: the earlier
  folder is set aside, the new one renamed in, then the earlier one discarded, so the folder never mixes two runs. A
  CSV folder that also holds other files is not replaced (a warning says so) and those files are left alone.
- Nothing after the document is written can make the run fail: a file that cannot be moved into place, an earlier
  output that cannot be removed, or a record that cannot be updated is a warning, and the document stays valid.
- `<document>.outputs.json` beside the document records which extras this run wrote, with the document's ID,
  revision and profile. Running again over the same document removes the extras that record lists and this run did
  not write again: one you no longer ask for, or one that failed this time (an older copy would read as current).
  Only files that record lists, under the extras' own names, are ever removed; anything else in the folder, including
  extras written by `generate_docs.py`, is left alone. An earlier output that cannot be removed is named in a
  warning and kept in the record (under `stale`), so the next run tries again. With no extras left, the record goes
  too, so HTML-only output is just the document. The record is a separate file: the document, its integrity check and publication are not
  affected by it.
- A batch keeps its `--also` choice, so `bidoc retry` writes the same outputs. With `--json`, `bidoc generate` lists
  the files in `extra_paths`.

## Details the source cannot give

A document's **Documentation details** (Power BI) or **Factory details** (Data Factory) are read-only in the page,
because saving the page would break the integrity check the library relies on. Record them at generation instead,
from a JSON file:

```json
{"reportLocation": "https://app.powerbi.com/groups/finance/reports/sales", "folder": "Finance / Monthly",
 "connections": [{"sourceType": "SQL Server", "server": "sql.example.local", "database": "SalesDW",
                  "username": "svc-pbi-refresh", "authentication": "Windows"}]}
```

```json
{"environment": "Production", "owner": "Data Platform", "factory": "", "runbook": "https://wiki/adf-sales",
 "sourceLocation": "https://dev.azure.com/contoso/data/_git/adf-sales (main)", "folder": "Sales", "notes": ""}
```

- `bidoc generate --details FILE` uses that file.
- Without `--details`, and in `bidoc batch` and the desktop app, a file named `<source name>.bidoc-details.json`
  beside the source is used when present: `Sales.pbix.bidoc-details.json` beside `Sales.pbix`, or
  `factory.bidoc-details.json` beside a `factory` folder. Keep it there and every regeneration records the same details.
- The fields are the engines' own (`pbi-doc-gen --metadata`, `adf-doc-gen --details`). Unknown fields, a file that is
  not JSON, or a value that holds a credential (below) stop the generation with a message naming the file; nothing
  is written. Record where a secret lives, never the secret.
- In the shared profile the same cleaning as the rest of the document applies: a personal file location (for example
  under `C:\Users`) is withheld, keeping the file name and a stable reference.
- The details are part of the document, not of its identity: adding or changing them makes a new revision of the
  same document.
- A value is refused when it holds a credential rather than a reference to one: a credential setting with a value
  (`Password=`, `pwd=`, `client_secret=`, `api_key=`, `AccountKey=`, `token=` and the like), a credential in a URL
  query (`?sig=`, `&token=`), a password in a URL (`user:password@host`), an `Authorization` header value or a bearer
  token. Account names, authentication types, Key Vault URLs and `@Microsoft.KeyVault(...)` references are fine. Both
  engines apply the same rules, and the error names the field and the file, never the value.

**Two environments.** The Factory details `environment` is text for readers of the document. `--environment` is
different: it files the document in the library and is part of its identity, so `Production` and `Test` are separate
documents. Neither sets the other. When the details name an environment that `--environment` does not, `bidoc` warns
and files the document as `--environment` says.

### Example: a factory with its details

```text
C:\Repos\
  adf-sales\                       the factory (its Git folder)
  adf-sales.bidoc-details.json     its details, beside it
```

`adf-sales.bidoc-details.json`:

```json
{
  "environment": "Production",
  "owner": "Data Platform",
  "factory": "contoso-prod / rg-data / adf-sales",
  "sourceLocation": "https://dev.azure.com/contoso/data/_git/adf-sales (main)",
  "runbook": "https://wiki.contoso.com/data/adf-sales",
  "folder": "Sales",
  "notes": "Credentials are in Key Vault kv-data-prod."
}
```

```powershell
bidoc generate --engine adf --kind adf_git --source "C:\Repos\adf-sales" --output-dir "C:\Documentation" --environment Production
```

```text
  validating: details from adf-sales.bidoc-details.json
  ...
Wrote C:\Documentation\adf sales--fe76dec4.html
```

Open the document and choose **Factory details** in the sidebar: the seven fields are shown as written, read-only.
For a Power BI source the same file sits beside the `.pbix` or `.pbip` (`Sales.pbix.bidoc-details.json`) with
`reportLocation`, `folder` and `connections`, and the values appear under **Document actions → Documentation
details**. `bidoc batch C:\Repos --output-dir C:\Documentation` and the desktop app pick up the same file.
Without `--environment Production` the document is still written, with this warning:

```text
  warning: adf-sales.bidoc-details.json gives the environment 'Production' for readers of the factory details, but the document is filed with no environment. Use --environment 'Production' to file it there; the environment is part of the document's identity.
```

## Pairing a live-connected report with its model

A report that connects live to a model elsewhere (Power BI service, Analysis Services) has no model of its own, so its
document lists the fields it uses as unresolved. Give it a model snapshot to resolve them against, per report:

- **In the report's details file**, beside it, with the documentation details under `documentation`:

  ```json
  {
    "documentation": {"reportLocation": "https://app.powerbi.com/groups/sales/reports/monthly", "folder": "Sales"},
    "model": {"path": "../Models/Sales.abf"}
  }
  ```

  For `Sales.Report` this is `Sales.Report.bidoc-details.json`; for `Sales.pbix`, `Sales.pbix.bidoc-details.json`.
  `bidoc generate`, `bidoc batch`, `bidoc retry` and the desktop app all read it. Either part may be left out. A file
  without a model keeps the flat form (the documentation fields at the top); a flat file that also has `"model"` is
  refused, so the two forms are never confused.
- **With `--model PATH`** on `bidoc generate`, for one report. It wins over the details file, and a note says the
  file's model was not used.

Rules:

- A relative `path` is resolved against the details file's own folder (beside the report: the report's folder), never
  the folder you run `bidoc` from. An absolute path works too.
- The model can be an ABF backup, a PBIX, a `.bim` file, or a model folder (TMDL, or one holding `model.bim`). An ABF or
  PBIX is extracted with the portable reader first.
- The pairing is checked before anything is extracted or generated: a model that is missing, unreadable or not a model
  stops that report with a message naming the details file (or `--model`), the model by its file name and the reason,
  never its full path. In a batch, that report fails on its own, nothing is extracted for it, and the others run; fix
  the file and `bidoc retry` it.
- An ABF or PBIX model's extract is only an input to that run: it is removed when the report succeeds, fails or is
  cancelled. A model that cannot be extracted is reported by name, with the error code, never the reader's own message.
- Nothing is guessed: a report is paired only with the model its file or `--model` names, never one found by name.
  Several reports can name the same model.
- A report that has a model of its own is never given another one: the pairing is refused.
- The document records which model was used and how it was chosen (`modelPairing` in its data, and a warning in the
  document), and says the pairing is **not verified** against the report's live connection: the model's server identity
  and how current it is are not checked, and an ABF backup is an offline snapshot.

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

### When pbi-tools fails on a file

An error such as `pbi-tools started but the extraction failed (exit code -9 (0xFFFFFFF7))` means pbi-tools launched and then
failed on that file; a missing or unusable pbi-tools is reported separately as `PREREQUISITE_MISSING`. The message quotes the
first lines pbi-tools printed that name a problem (its stack trace comes last and is not the cause); the whole output stays in
`pbi-tools.log` in the item's workspace. The platform never retries with another extractor after a failure: choose
`--pbix-backend pbixray` yourself if you want the portable reader.

To compare the extractors on one file without sharing it, run on the Windows machine that has the file:

    python scripts/diagnose_extractors.py "C:\path\file.pbix" --pbi-tools "C:\path\pbi-tools.exe" --report diagnose.json

It runs pbi-tools as the platform starts it, as the old standalone tool started it (stdin and process group differ), on a copy
in a shorter folder (the report gives both path lengths; `renamed-copy` if no shorter folder was available), and the portable
reader. A timeout or Ctrl+C ends each extractor's whole process tree. The report holds exit codes, durations, the first problems
pbi-tools reported and its last lines, and the PBIX's size bucket and standard part names. Redaction is best effort, and
free-text names are not guaranteed to be removed: paths, URLs, e-mail addresses, GUIDs and connection-string values are replaced,
and so are the file's name, its folders, the user name and any `--redact-also NAME` (use it for server, database or report names).
Inspect the report before sharing it. The script shows how the extractors differ; it does not by itself establish why a file fails.

## Batches and history

`bidoc batch` takes the same `--profile`, `--include-query-code`, `--environment`, `--business-area`, `--owner`,
`--also` and `--extract-timeout` as `bidoc generate`, for every input, and `--backend auto|pbixray|pbi-tools` (or
`--pbixray`) to choose the PBIX extractor for the whole batch. A batch keeps these choices: `bidoc retry` uses the
same outputs and the same extractor. Details come from each input's own `<source name>.bidoc-details.json`.
Errors and warnings in batch history, `--json` output and the desktop app never carry a personal location or a
credential: written messages are filtered with the shared-publication cleaner, and an unexpected error is reported by
its type only. Two
`generate` option sets have no batch form: `--model` (in a batch, each live-connected report names its model in its
own details file; see "Pairing a live-connected report with its model"), and `--title`/`--description`/`--tag`, which
describe one document (a batch titles each document after its source).

`bidoc batch` and the desktop app recognise inputs by shape:

- `.pbix` files;
- PBIP project folders, or a `.pbip` file whose `.SemanticModel` folder sits beside it (a lone
  pointer is refused);
- TMDL and PBIR folders, and `model.bim`;
- an Analysis Services tabular model: its `Model.bim`, or the script SSMS writes for **Script Database as → CREATE To**
  (`.xmla`), at compatibility level 1100 and later. A `.xmla` file is read only when it is named or added itself;
  what is read and what is not is in `components/power-bi/README.md`;
- pbi-tools extracts;
- Data Factory Git folders, and ARM or resource JSON.

**Any other folder is searched** (`bidoc_generator/discovery.py`, used by the command line, the desktop review and the desktop
submission alike), subfolders included, for `.pbix` and `.abf` files, `.bim` models and project folders:

- a recognised project folder (PBIP, TMDL, PBIR, pbi-tools extract, Data Factory Git folder) is one input and is not searched
  inside, so a model's own files or a factory's individual JSON files are never queued separately. A Data Factory Git folder
  needs JSON in at least one of its `pipeline`, `dataset`, `linkedService`, `dataflow`, `trigger` or `factory` folders, so a
  reports folder that merely has a subfolder with one of those names is still a container;
- other files are ignored, including stray `.json` (only an explicitly selected JSON file is treated as a Data Factory export)
  and `.xmla` (most are processing scripts; a CREATE script of a database is read when it is selected itself);
- a **selected folder of loose Data Factory resource JSON** (saved from ADF Studio's "view code", not in a Git layout) is one
  Data Factory input when the search finds nothing else in it and its own `.json` files hold at least one pipeline or data
  flow, judged by content as the Data Factory engine reads them. It is the input `bidoc generate --kind adf_resources` takes.
  Only a selected folder is judged this way, never one met while searching, and the generator's own files (identity and
  details files, output records) are not read to decide;
- the output folder, the generator's own folder, `.git`, `.venv`, `venv`, `node_modules` and similar are left out, as a
  search target and as a selection (selecting one is a failed item that says why);
- **links are never followed**: a symbolic link or Windows junction is reported and skipped whether it is a selection, a folder
  or a file; a selected one is a failed item. (`bidoc batch` passes paths as given, so a selected link is seen, not hidden.)
  OneDrive-style cloud placeholders are ordinary files, not links. Recognising a project obeys the same rule: a linked or
  left-out `pipeline` folder, `Model` folder, `.SemanticModel` entry, `definition` folder or JSON file never counts towards
  making its parent a project, so it cannot hide the reports beside it;
- paths are normalised in the shared discovery, once, so the command line and the desktop name every input with the same string
  (relative paths and Windows short names such as `RUNNER~1` included);
- the limits cover the whole call, not each selection: at most 1000 inputs and 20000 folder listings in total, counting the
  listing made to recognise a project and the look inside a Data Factory folder's parts. At the limit discovery stops, keeps what
  it found and adds one failed item saying so;
- a folder that cannot be read, an empty scan and a scan that hit a bound are *failed items* with a message, so they appear in
  the history and the exit code while every readable input still runs. Retrying one searches that folder again; if it now holds
  several inputs the retry says so, and the folder should be queued again as a new batch;
- selections keep the order given; a folder's finds are sorted by path; the same file reached twice is queued once; documents
  are labelled with the path below the selected folder's parent. A document that cannot be published is named
  `<title>--<tag of the source path>.local.html`, so two such inputs with the same file name never share an output file and
  rerunning one input refreshes its own file.

`bidoc batch` prints what it found before it starts. The desktop review lists the same inputs and **Generate queues exactly that
reviewed list** (a snapshot: files added after the review are not included; review again to pick them up). The review remembers
the inputs and the output folder it was made from: changing either in the window withdraws the review (including one still
running, whose answer is dropped when it arrives), and the server refuses to queue an old review for a different list (409). The offline hub is a
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

- a new batch with native file and folder pickers, and a choice of the other outputs to write beside each document
  (JSON extract, Word document, CSV, agent context; the same as `--also`);
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

## Documents above the publication limit

The library's 25 MiB limit applies to publication, not to generation. A document above it is still written and works locally; the item finishes as local-only with a warning giving the size, the limit and the next step (`ARTIFACT_TOO_LARGE`), and `bidoc generate --profile shared` exits non-zero because the result cannot be published. `bidoc publish` checks the target library's advertised limit before uploading and says the file is unchanged. The hub export is a local folder and is not subject to the publication limit. Above a 256 MiB generation ceiling generation fails (`CONTRACT_VIOLATION`). The model payload is embedded twice (viewer and manifest), so documents are about twice the payload size.

### Large samples to try

`python scripts/large_samples.py` lists three large inputs and `python scripts/large_samples.py ID ...` (or `--all`) fetches or
builds them into `samples/downloads/large/`. They are for trying the limits by hand: they are described in
`samples/large-manifest.json`, pinned by size and SHA-256, never committed, and not part of the acceptance corpus that continuous
integration downloads. The PBIX is unpacked from a `.7z` archive, which needs `python -m pip install py7zr`.

| Id | Input | What `bidoc generate --profile local` does |
|---|---|---|
| `contoso-10m` | A 460 MiB PBIX with 10 million sales rows (SQLBI's public Contoso sample) | Completes; the document is under 1 MiB. A document's size follows the metadata (tables, measures, queries, report pages), not the rows |
| `large-document` | A 16 MiB synthetic model: 1800 measures of about 9 KB of DAX each | Writes a 110 MiB document and exits 0, with the `ARTIFACT_TOO_LARGE` warning that it is above the 25 MiB publication limit |
| `over-ceiling` | A 45 MiB synthetic model: 5000 such measures | Fails with exit code 4 and writes nothing: the document would be about 300 MiB, above the 256 MiB generation ceiling |

The platform's document is larger than the engine's own (`pbi-doc-gen --model`) for the same input, because the envelope carries
the model a second time in its manifest: 110 MiB against 51 MiB for `large-document`, and 0.8 MiB against 0.5 MiB for
`contoso-10m`. For `over-ceiling` the engine alone writes 142 MiB.

## Portable offline export

`bidoc export-library OUTPUT INPUT...` writes a folder that works without a network or a
library server, opened straight from disk. See `docs/portable-export.md`.

## Worker mode (optional R3)

`bidoc worker connect URL`, then `bidoc worker run` processes jobs that people upload to a
library, one at a time, with the same engines. See `docs/workers.md`.
