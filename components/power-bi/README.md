# Power BI Documentation Generator

Generates one self-contained, interactive HTML page per Power BI report: what the
semantic model contains, where its data comes from, how the report uses the model,
what could be removed, and what to watch out for. A **report library** page
(`pbi-home.html`) ties the pages together.

It reads Power BI project files (PBIP) directly, or PBIX files through
[pbi-tools](https://pbi.tools). Python 3.10+ standard library only: no `pip install`,
no server, no connection to any database or to the Power BI service. The output
works offline and can be emailed, put on SharePoint or committed to a repo.

## Contents

1. [Quick start](#quick-start)
2. [Examples with the sample files](#examples-with-the-sample-files)
3. [Supported inputs](#supported-inputs)
4. [What is in a report page](#what-is-in-a-report-page)
5. [Sources](#sources)
6. [Cleanup: which columns and measures can be removed](#cleanup-which-columns-and-measures-can-be-removed)
7. [Warnings](#warnings)
8. [The report library and report details](#the-report-library-and-report-details)
9. [Other outputs: CSV, JSON, Word, agent context](#other-outputs-csv-json-word-agent-context)
10. [Command-line reference](#command-line-reference)
11. [Limitations](#limitations)
12. [Development and testing](#development-and-testing)
13. [Repository layout](#repository-layout)

## Quick start

### A folder of PBIX files (Windows)

Install Power BI Desktop and **pbi-tools Desktop**, then:

```powershell
python generate_docs.py --pbix-folder "C:\Reports" --output-dir "C:\Documentation" --pbi-tools "C:\Tools\pbi-tools\pbi-tools.exe" --recursive
```

Open `C:\Documentation\pbi-home.html`. Each PBIX gets its own page, named
`<report>--<hash>.html` so reports with the same name in different folders do not
collide. A file that fails is listed in the summary and does not stop the others;
extraction logs are in `pbix-logs\`. Use `--pbix FILE` for a single file.

The batch asks pbi-tools for its single-file model layout and also accepts its
folder and TMDL layouts. Some older PBIX files make pbi-tools ignore the extract
folder and write next to the PBIX; the batch moves that output into its own
working folder. If a previous run left such a folder beside a PBIX, delete it
before rerunning.

### A Power BI project (PBIP)

```
python generate_docs.py --project path/to/Sales.pbip --output docs/Sales.html
```

`--project` also accepts the project folder or either artifact folder
(`Sales.SemanticModel`, `Sales.Report`); the other half is found through
`definition.pbir`. To name them yourself:

```
python generate_docs.py --model Sales.SemanticModel --report Sales.Report --output docs/Sales.html
```

To save a PBIX as a project: in Power BI Desktop choose **File → Save as** and the
`.pbip` type. Recent Desktop versions save the model as TMDL and the report as
PBIR by default; older versions may need the preview features enabled under
**File → Options and settings → Options → Preview features**.

## Examples with the sample files

`pbix-samples/` holds 15 real Power BI files from Microsoft (see its README).
Each example below says what to run and where to look. Commands are for
PowerShell from the repository root, with pbi-tools in `pbi-tools\`.

### 1. Document all the samples and open the library

```powershell
python generate_docs.py --pbix-folder pbix-samples --output-dir pbix-samples\documentation --pbi-tools pbi-tools\pbi-tools.exe
```

Open `pbix-samples\documentation\pbi-home.html`. Each report card shows its
mode, analysis coverage, cleanup candidates and sources. Select a source tag to
filter the cards, or open **Sources** in the left rail to see which reports share
a server or file. The two older demos (2018 October, 2019 July) are the ones
pbi-tools writes next to the PBIX; the batch picks that up without any action.

### 2. One report: where does the data come from?

```powershell
python generate_docs.py --pbix "pbix-samples\COVID Bakeoff.pbix" --output-dir pbix-samples\documentation --pbi-tools pbi-tools\pbi-tools.exe
```

In **COVID Bakeoff**, go to **Data & sources**:

- **Sources**: one row per source (web APIs, CSV files, SQL Server and Excel),
  with the tables and pages each feeds. Select a row for its connection, queries
  and code.
- **Primary sources**: the same inputs per report page. **Export primary sources
  CSV** saves the list.
- **Lineage**: select a source or a page to highlight every path between them.

Compare **Human Resources Sample PBIX** for SQL Server native queries (each object
named in the SQL is listed, e.g. `hr.bu`), **2018SU10 Blog Demo - October** for an
OData feed, **PerformanceAnalyzerExportReport** for JSON files and
**Supply Chain Sample** for Azure Blob Storage.

### 3. What can I remove?

Open **Adventure Works DW 2020 → Review issues → Cleanup review** and choose
**Deletion candidate**: 26 columns with no reference in the model or report. Each
row says why, and **Export evidence at column/page grain** saves the list. Switch
the assessment to **Keep** to see what holds the rest (relationships, measures,
sort-by columns…).

Other samples show the rules at work:

- **2020SU09 Blog Demo - September**: the `Online Sales` columns stay in
  **Review** because the report uses `Online Sales[Spending]`, which the model
  does not have.
- **2020SU11 Blog Demo - November**: 50 candidates, even though a measure uses
  `COUNTROWS('Online Sales')`. Counting rows does not depend on any column.
- **Revenue Opportunities**: every table is typed into the file ("Entered data"),
  so there are no external sources, but still 34 candidates.
- **Overview** in any report lists duplicate measures (the same DAX under two names)
  and how many measures, columns and tables have descriptions.

### 4. What breaks if I change this?

Open **Adventure Works DW 2020 → Impact & usage → Impact inspector**, choose a
column or measure, and follow its paths down to the visuals that show it.
**Usage matrix** shows which tables and columns feed each page; select a cell for
the evidence. Use the **Report page** selector at the top to focus every view on
one page.

### 5. What is on each page?

- **Corporate Spend → Pages & visuals → Page layout**: each page drawn from its
  saved layout, visuals named by type and first field ("Card · Var to Plan %"),
  with the measures (Σ) and columns each uses. Select a visual for its bindings
  and filters.
- **Sales & Returns Sample v201912 → Pages**: dozens of buttons, shapes and images
  are folded into one "decorative visuals" row per page, so the data visuals stand
  out. Custom visuals show their real names ("Mapbox Visual (custom)").
- **Filters** and **Field manifest** list every filter and every field the report
  uses, and where.

### 6. What is wrong with this report?

Open **Review issues → Warnings**:

- **Regional Sales Sample**: "A formatting rule refers to Page Details[Facility],
  but table 'Page Details' does not exist", with the visuals it is on, and a page
  filter on a column that no longer exists.
- **Sales & Returns Sample v201912**: `Sales[Dates]` is missing but used by
  report-level filters, bookmarks and a Q&A visual.
- Model warnings: inactive relationships and bidirectional filters in
  **Adventure Works DW 2020**, disconnected tables in **Supply Chain Sample**.

### 7. Record where a report lives and who connects

In any report, open **Report details**: set the original location (for example
`\\fileserver\Reports\Finance\Corporate Spend.pbix`), a library folder
(`Finance / Monthly`) and connection notes such as the service account. Choose
**Download updated HTML** and replace the file. The library groups the report
under that folder, and the details survive the next batch run.

### 8. JSON, CSV, Word and agent outputs (PBIP)

These need a Power BI project. Open a sample in Power BI Desktop, choose
**File → Save as**, pick the `.pbip` type (for example
`C:\Temp\Corporate Spend.pbip`), then:

```powershell
python generate_docs.py --project "C:\Temp\Corporate Spend.pbip" --output docs\CorporateSpend.html --json docs\CorporateSpend.json --csv docs\CorporateSpend-columns.csv --word docs\CorporateSpend.docx --agent
```

This writes the HTML, the analysis as JSON, the column and page inventory as CSV,
a Word handover document, and `CorporateSpend.agent.md` for LLM agents.

### 9. Connectors the real samples do not cover

```
python tests/samples/build_enterprise_sample.py sample-output
python tests/samples/build_files_sample.py sample-output
```

`sample-output\Network Operations.html` shows Teradata (navigation, native SQL,
ODBC), Oracle (service parameter, TNS alias, `Value.NativeQuery`), Databricks, a
Power Platform dataflow and DirectQuery/Dual storage. `sample-output\Field
Services.html` shows SharePoint lists and files, local, UNC and mapped-drive files,
and a folder combined with "Combine files". Both use invented names;
`sample-output/` is ignored by git.

## Supported inputs

| Input | Read |
|---|---|
| Semantic model | TMDL (`definition/` folder), TMSL (`model.bim`), and pbi-tools extracts in Raw, folder and TMDL layouts |
| Analysis Services tabular model | `Model.bim` at compatibility level 1200 or later, or the script SSMS writes for **Script Database as → CREATE To** (also CREATE OR REPLACE and ALTER; usually saved as `.xmla`, and JSON at these levels). Pass either to `--model`. Sources come from the model's data sources: provider connection strings with SQL partitions, and structured data sources named in Power Query (`#"SQL/server;database"`) |
| Analysis Services tabular model, levels 1100 and 1103 | The XML definition: `Model.bim`, or the SSMS **Script Database as → CREATE To** script (`.xmla`). Tables, columns, partitions and their data sources, measures, relationships, hierarchies and roles are read; KPIs, perspectives, translations and role members are not. Multidimensional cubes use the same XML and are refused by name |
| Report | PBIR (`definition/pages/...`), the legacy single-file `report.json` / PBIX `Layout`, and pbi-tools' split report folders. PBIR inside a PBIX is read from the file itself |
| Legacy models | Pre-2019 models whose tables read `SELECT * FROM [Table]` from a `Microsoft.PowerBI.OleDb` source: the Power Query packed in that source is traced instead |

You can supply a model, a report or both:

| Mode | What you get | What it will not claim |
|---|---|---|
| **Combined** (model + report) | Everything below. | — |
| **Model only** | Tables, measures, relationships, sources, security, warnings. | Never calls anything unused, since reports are unknown. |
| **Report only** | Pages, visuals, filters, bookmarks and a field manifest: every field the report needs. | Cannot confirm the fields exist or trace them to a source. |

A PBIX with no embedded model (a report on a published dataset) is documented as
report only.

## What is in a report page

The page opens on **Overview**. The left rail has six sections:

| Section | Views |
|---|---|
| Overview | Counts, sources at a glance, usage summary, documentation coverage (measures, columns and tables described) and duplicate measures |
| Data & sources | Tables, Columns, Measures, Relationships, Lineage, Sources, Primary sources, Security |
| Pages & visuals | Page layout, Pages, Filters, Field manifest |
| Impact & usage | Impact inspector, Usage matrix, Usage |
| Review issues | Cleanup review, Warnings |
| Report details | Report location, library folder and connection notes (see [below](#the-report-library-and-report-details)) |

A bar above each view holds the **Report page** selector, which filters usage views
and exports to one page, and an analysis-coverage badge; select the badge to see
what limited the analysis. Cleanup, counts, relationships, security and warnings
always cover the whole report.

Highlights:

- **Relationships** draws the model as a diagram with zoom and a focus-table picker.
- **Lineage** shows sources → model tables → report pages; select a node to highlight its paths.
- **Page layout** draws each page from saved visual positions (not rendered charts),
  colours visuals by kind and lists the measures (Σ) and columns each one uses.
  Untitled visuals are named by type and first field ("Card · Revenue"); custom
  visuals by their package name ("Mapbox Visual (custom)").
- **Pages** lists each page's data visuals with their fields; buttons, shapes and
  images without data are folded into one "decorative visuals" row.
- **Measures** are grouped by display folder, with DAX, format string, the pages
  that use them and their dependencies.
- **Impact inspector** shows everything downstream of a column or measure, down to the visuals.

Raw page IDs (`ReportSection…`) appear only in tooltips and exports, unless two
pages share a name.

## Sources

Sources are traced statically through Power Query (M) and embedded SQL, including
shared queries, parameters, custom functions and "Combine files" helper queries.
Nothing is executed and no connection is made.

**Sources** lists one row per external source with its model tables, report pages,
reporting usage and identification status. **Primary sources** lists each external
input per report page and exports it as CSV.

| Kind | Connectors |
|---|---|
| Databases | SQL Server (including native queries), Azure SQL / Synapse, Oracle (service, TNS alias, native SQL), Teradata (navigation, native SQL), Snowflake, Databricks, ODBC |
| Files | Excel, CSV, JSON, XML, Parquet, PDF, Access; on local drives, UNC shares and mapped drives; whole folders ("Folder · (all files)") |
| SharePoint and OneDrive | Lists (Online and on-premises; lists picked by ID show a short ID), files through SharePoint.Files, SharePoint.Contents or a document URL |
| Services | Web / API, OData, Azure Blob Storage, Azure Data Lake, Power BI and Power Platform dataflows |
| Inside the model | Entered data, data generated in Power Query, calculated (DAX) tables. Shown, but not treated as external sources |

| Identification | Meaning |
|---|---|
| Resolved | Connection and object identified from the code. Not a live check. |
| Partial | Object identified, but some context is external or uncertain (for example an ODBC DSN, or a source used only by a bookmark with no page). |
| Unresolved | The code could not be traced to an object. |

Unquoted Oracle names are folded to upper case, so `billing.tariff` and
`BILLING.TARIFF` are one source. Credentials in connection strings and URLs are
never copied into source identities.

## Cleanup: which columns and measures can be removed

**Cleanup review** gives each column and measure an assessment:

| Assessment | Meaning |
|---|---|
| **Keep** | Used by the report, or needed inside the model: a measure (even an unused one), calculated column or table, relationship, hierarchy, sort-by column, RLS filter or dynamic format. |
| **Deletion candidate** | No reference found in the supplied model and report. Check other reports, Analyze in Excel users and composite models before deleting; the tool cannot see them. |
| **Review** | Usage cannot be settled, for the reasons below. |

What holds columns in Review:

- **A whole-table dependency**, such as `COUNTROWS(DISTINCT(T))` or `FILTER(T, …)`,
  holds every column of T. A plain `COUNTROWS(T)` does not, since removing a column
  cannot change a row count.
- **A missing field on a table that exists**, such as a report binding to `Sales[Old]`,
  holds that table.
- **A reference with no table** that cannot be resolved, or an unreadable
  definition, holds everything.
- **A reference to a table that does not exist** is reported but holds nothing: it
  cannot depend on a column that exists.
- **A visual whose data query cannot be read** holds everything.

Bookmark fields count as used. Only their page is uncertain, which does not change
a deletion decision. Reports in the older layouts get the same assessment as PBIR.

Duplicate measures (the same DAX under different names, ignoring whitespace,
comments and case) are listed as well.

## Warnings

Warnings list inactive relationships, bidirectional filters, missing date tables,
disconnected tables, legacy report layouts and **broken bindings**: fields the
report uses that the model does not have. Each broken binding says where it is
used ("Used on: Pipeline Trends — Page filter"), and a reference used only by
formatting reads "A formatting rule refers to …".

## The report library and report details

Every run refreshes `pbi-home.html` beside the output. It has three views:

- **Reports**: a card per report with its mode, analysis coverage, cleanup
  candidates, duplicate measures and sources, grouped by folder.
- **Sources**: which reports use each server, database or file.
- **Manage library**: refresh the list from a folder and download the refreshed page.

It lists the HTML files directly in its folder. To rebuild it from existing files
without reading any Power BI files:

```
python generate_docs.py --catalog "C:\Documentation"
```

In a report page, **Report details** records the original report location, a
library folder (such as `Finance / Monthly`) and connection notes (connection name,
authentication, username or service account). These are notes you maintain, never
passwords, so don't put secrets in them. To keep your edits, choose **Download
updated HTML** and replace the original file. Details survive regeneration to the
same file name and PBIX batch reruns. They can also be applied with
`--metadata FILE.json`:

```json
{
  "reportLocation": "C:\\Reports\\Finance\\Sales.pbip",
  "folder": "Finance / Monthly",
  "connections": [{"sourceType": "SQL Server", "server": "sql01", "database": "Reporting",
                   "connectionName": "Reporting warehouse", "username": "CORP\\report_reader",
                   "authentication": "Windows"}]
}
```

## Other outputs: CSV, JSON, Word, agent context

- **CSV exports in the page**: the column and page inventory, the table source
  summary, primary sources, source objects (with or without code), all M queries,
  cleanup evidence and measure assessments. File names start with the report name.
- **`--csv PATH`**: the full column and page inventory, which needs a model.
- **`--json PATH`**: the whole analysis as JSON. Every HTML page also embeds it
  (`const DATA = {…}`), so the pages double as machine-readable records.
- **`--word PATH`**: a `.docx` narrative of the same analysis for handovers and sign-off.
- **`--agent [PATH]`**: a compact Markdown version for LLM agents.

All renderings come from one analysis, so they cannot disagree.

## Command-line reference

| Flag | Meaning |
|---|---|
| `--project` | `.pbip` file, project folder, or either artifact folder |
| `--model` | `.SemanticModel` folder (TMDL or TMSL), its `definition/` folder, or `model.bim` |
| `--report` | `.Report` folder (PBIR or legacy `report.json`) |
| `--output`, `-o` | HTML path (default `<name>.html`) |
| `--title` | Document title |
| `--json`, `--csv`, `--word`, `--agent` | Extra outputs (see above) |
| `--pbix FILE` / `--pbix-folder FOLDER` | Document PBIX files through pbi-tools |
| `--output-dir` | PBIX output folder (default `<input>/documentation`) |
| `--recursive` | Include PBIX files in subfolders |
| `--pbi-tools EXE` | Path to `pbi-tools.exe` |
| `--extract-timeout` | Seconds per PBIX (default 600) |
| `--catalog FOLDER` | Rebuild `pbi-home.html` from existing pages |
| `--metadata JSON` | Apply report details from a file |
| `--no-hub` | Do not refresh `pbi-home.html` |

## Limitations

- **Static analysis.** DAX and M are read, not executed. Values decided at runtime,
  such as field parameters or dynamic SQL, may not resolve, and are then marked
  Partial, Unresolved or Review instead of guessed.
- **One report's view.** A deletion candidate is unused by *this* report and model
  only. Other reports on the same dataset, Excel users and composite models are invisible.
- **No live checks.** Sources are what the code names; the tool does not confirm
  that they exist or which view reads which table.
- **Page layout** is a schematic from saved positions, not a rendering.
- **pbi-tools** needs Windows and Power BI Desktop. PBIP projects need neither.
- **Not tested against real files:** DirectQuery against a live database and live
  connections to a published dataset. Synthetic samples cover their code patterns.

## Development and testing

The tool also installs as a package (standard library only, no dependencies):

```
pip install .
pbi-doc-gen --project path/to/Sales.pbip --output docs/Sales.html
```

`pbi-doc-gen` accepts exactly the same options as `python generate_docs.py`. The
package version is `pbidocgen.__version__`.

```
python tests/run_ci.py
```

This runs the full suite (171 tests at the time of writing, including checks that
run the generated JavaScript under Node.js when it is installed) and fails if
tests are skipped. There is no CI workflow; run it locally.

Browser checks (Playwright and Chromium):

```
npm install --no-save playwright@1.62.1 && npx playwright install chromium
python tests/check_browser.py
node tests/browser_catalog.cjs
```

`check_browser.py` builds the synthetic fixture and runs `browser_review.cjs` (every view, exports, comparison)
and `browser_navigation.cjs` (the finder, object links, Back and Forward, the Tables search and navigation from
a framing page). In this repository the `frontend` CI job runs it.

Samples for trying the tool or checking changes:

| Script | What it does |
|---|---|
| `tests/samples/build_retail_sample.py OUT` | Retail sample: SQL Server, SharePoint Excel, CSV |
| `tests/samples/build_enterprise_sample.py OUT` | Teradata, Oracle, ODBC, Databricks, a dataflow, DirectQuery and Dual |
| `tests/samples/build_files_sample.py OUT` | SharePoint lists and files, Windows files and a combined folder |
| `tests/samples/check_pbix_samples.py SRC` | Runs the 29 reports pre-extracted in [pbi-tools/pbix-samples](https://github.com/pbi-tools/pbix-samples) (pinned commit in the script) |
| `tests/samples/check_sources.py FOLDER` | Reports detected sources for your own PBIP projects |

Real sample PBIX files are in `pbix-samples/`; see its README for where they come from.

### Viewing inside a documentation library

When a page is shown inside a library shell (an iframe), a small listener accepts `bi-doc-viewer` protocol v1 messages from the parent window only, to open a specific report object (for example a measure or an activity) when the shell asks. It does nothing when the page is opened directly, and it never loads anything or runs code from a message.

## Repository layout

```
generate_docs.py          command line
pbidocgen/
  project.py              finds the model and report in a .pbip / folder
  model_parser.py         TMSL/TMDL model → normalised model; DAX dependencies
  tmdl_reader.py          TMDL folder → the same shape as model.bim
  pbitools_folder.py      pbi-tools folder layouts → model.bim shape
  legacy_mashup.py        Power Query packed in legacy provider data sources
  report_parser.py        PBIR report → pages, visuals, bindings, filters, bookmarks
  extracted_report.py     legacy report.json / PBIX Layout / pbi-tools report folders
  custom_visuals.py       custom visual display names
  pbix_batch.py           PBIX batch through pbi-tools
  m_sources.py            Power Query tracer
  external_sources.py     files, folders, SharePoint, web, OData, storage
  sql_sources.py          objects named in embedded SQL
  partition_sources.py    one traced source per model partition
  source_*.py, primary_sources.py   source inventories and labels
  linker.py               joins report and model; usage verdicts, broken bindings
  column_usage.py         Keep / Deletion candidate / Review assessments
  quality.py              documentation coverage, duplicate measures
  renderer.py             builds the analysis and writes the HTML
  template.html, explorer.js, explorer.css, report_metadata.js   the report page
  catalog.py, catalog.html  the report library
  word_writer.py, agent_writer.py   Word and agent outputs
tests/                    unit tests, browser checks and samples
```
