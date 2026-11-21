# sqlagent-doc-gen

Static analysis of scripted SQL Server Agent jobs (`sp_add_job` /
`sp_add_jobstep` / schedule procedures) for bi-doc-platform. The engine reads
SSMS-style job scripts, correlates jobs, steps and schedules (including
`@jobId OUTPUT` variables and shared schedules), analyses SSAS processing
commands (XMLA `<Batch>` / TMSL JSON) and T-SQL step bodies, and emits a
native JSON payload plus deterministic review findings.

**This engine never connects to a server.** It analyses scripts as text.

## Usage

```bash
python -m sqlagentdocgen path/to/jobs/                              # JSON payload to stdout
python -m sqlagentdocgen path/to/jobs/ -o payload.json              # full payload to file
python -m sqlagentdocgen path/to/jobs/ --coverage-only              # shareable counts only
python -m sqlagentdocgen path/to/jobs/ --html docs.html --also json,word,csv,agent
```

`--coverage-only` prints only aggregate counts and warning texts — never job
names, command text, or file content — so real scripts can be validated
without anything sensitive leaving your machine.

`--html` renders a self-contained interactive document (Overview · Jobs ·
Job detail with the execution flow graph · Processing · Schedules ·
Review issues · Coverage · Job details). `--also` adds the extra renderings
next to it: the JSON payload, a Word document, CSV inventories (jobs, steps,
operations, schedules, issues, entities) and an agent context markdown.
Every rendered output passes through the redaction safety net (`redact.py`)
before it is written: passwords and keys as `name = value` in connection strings and T-SQL (including `N'…'`
literals and parameters such as `@rmtpassword`; an unquoted value runs to `;` in a connection string and to the
next space in T-SQL, and `NULL` is not a secret), URL credentials and SAS tokens, bearer tokens, `-P <value>`, and
`-P<value>` on a line that runs `sqlcmd`, `bcp` or `osql`, continuation lines included (elsewhere `-Path`, `-Port`
and `-PassThru` are ordinary PowerShell parameters, and `Invoke-Sqlcmd` is a cmdlet), a PowerShell
`-Password <literal>` (`Invoke-Sqlcmd`, for one), `dtexec /De[crypt]`, PowerShell `ConvertTo-SecureString '…' -AsPlainText`, and backup passwords
(XMLA `<Password>`, TMSL `"password"`) are replaced with `[redacted]`. A `@variable` that holds a secret is left
as it is: it names where the secret is. Redaction counts only what it actually replaced.

## What it understands

- `sp_add_job`, `sp_add_jobstep` (TSQL, SSIS, ANALYSISCOMMAND, ANALYSISQUERY,
  CmdExec, PowerShell subsystems), `sp_add_jobschedule`,
  `sp_add_schedule` + `sp_attach_schedule` (shared schedules),
  `sp_detach_schedule`, `sp_add_jobserver` (MSX/TSX registration),
  `sp_update_job` (final enabled state), `IF EXISTS ... sp_delete_job`
  recreate preambles.
- XMLA `<Batch>`: Transaction/ProcessAffectedObjects attributes, `<Parallel>`,
  `<ErrorConfiguration>`, Process (all types; ProcessAdd out-of-line Bindings
  count as reads), Backup, Restore, Alter, Create, Delete, Synchronize, Detach,
  Attach, ClearCache. A Synchronize targets its database on the step's server
  and reads it from the server its source connection string names (only the
  server is kept from that string, never credentials or other settings).
  A Restore targets the database it names (`DatabaseID`, else `DatabaseName`)
  on the step's server.
- TMSL JSON: refresh (type + database/table/partition objects),
  createOrReplace, alter, delete, backup, restore, sequence envelopes.
- T-SQL step bodies via `adf-doc-gen`'s token-based harvester (tables read /
  written, procedures executed — never delete).

## Review findings

Findings are evidence about the scripts, not instructions. Each one states only what a script can show:

| Code | Severity | What it says |
|---|---|---|
| `job-failure-unnotified` | warning | *Potentially unnotified failure*: a failure path ends with no e-mail, net send or pager notification scripted. A script cannot show external monitoring, so this does not claim there is none. |
| `job-owner-sa` | warning | The job owner is `sa`. |
| `step-proxy` | info | A non-T-SQL step uses a configured proxy. Owner and proxy are reported as separate facts: the owner is not claimed to be the identity every step runs as. |
| `job-owner-missing`, `job-not-created` | info | Likely a partial script. |
| `job-duplicate` | warning | The same job is created in two files (dev and prod script-outs, say), or twice in one file without being deleted in between. Each definition is documented as its own job, never merged; the later one's identity carries its file (`name:nightly@prod.sql`). Alternatives under `IF` / `ELSE` in one file are one job, and a job deleted and created again in one file is replaced. |
| `job-deleted` | info | The script creates the job and then deletes it; the job is not documented as one that exists after the script runs, and its schedules no longer list it. |
| `job-disabled`, `job-unscheduled` | info | The final scripted state, after every `sp_update_job` in script order. |
| `job-schedules-disabled` | info | The job has schedules, but every attached schedule is scripted `@enabled = 0` (an unscripted `@enabled` is msdb's default, enabled). |
| `step-no-retry` | warning | An SSAS processing step has `retryAttempts = 0`. |
| `step-local-ssas` | info | An Analysis Services step has no `@server`, so it targets the local instance. |
| `process-full-database`, `process-full-granularity` | info | `ProcessFull` at database or cube scope while no job in this script set processes at partition granularity. |
| `schedules-coincide` | info | Jobs that process the same SSAS database have schedules with the same frequency and start time (or share one schedule). Jobs with no common processing target are not compared. Scripts carry no durations, so this is about start times only. |

Anything the parser cannot read is counted in **Coverage** (`unrecognisedStatements`, `unparsedSteps`,
`unknownOperations`, skipped files) and listed as a warning; it is never dropped silently.

## Shared documents

`bidoc generate --profile shared` (and the library) withhold every step's `command`, whatever its subsystem: XMLA,
TMSL, T-SQL, PowerShell and CmdExec text does not reach a shared document unless the publisher opts in with
`--include-query-code`. The document is rendered from the structured
fields instead (operations, processed objects, harvested tables, schedules, endpoints). Infrastructure names are kept,
as Data Factory linked-service endpoints are: job and step names, the SSAS `@server`, `@database_name` and the
entities. The cleaning of other fields (personal paths, credentials in text) is best effort, as it is for the other
engines; it is not a guarantee.

## Links to Power BI

In the library and the offline catalogue, a job step is linked to the Power BI sources it feeds
([relationship rules `rel-rules/2`](../../docs/relationships.md#detected-relationships-rel-rules2)): a step that
**processes or refreshes** an Analysis Services model is upstream of every Power BI source that reads that model (exact
when the job names the database as TMSL does and the report is live-connected; *possible* for an XMLA `DatabaseID`,
which is usually but not always the model's name), and a T-SQL step that writes a table is upstream of the sources that
read it (*possible* at most: a T-SQL step runs on the job's own server, which the script does not name). Backups,
restores, other model commands and stored-procedure calls are not links.

## Real-world corpus

Committed tests use synthetic and adversarial fixtures only. Public job scripts are checked by hand, never vendored:
[`samples/sql-agent-corpus.json`](../../samples/sql-agent-corpus.json) pins each file by commit and SHA-256 with its
license and expected coverage counters, and `python scripts/sql_agent_corpus.py` downloads, verifies and compares them
(counts only in its output). The expected baseline is DarlingData's *sp_WhoIsActive Logging* job at `unparsedSteps = 1`
(its step command is a concatenated variable). Run it after any change to the tokenizer, parser or registry; a
difference is investigated before anything else.

## Names and command forms

- Procedures are recognised however they are qualified: `msdb.dbo.sp_add_job`, `msdb..sp_add_job`, `dbo.sp_add_job`,
  bracketed or bare.
- A command built only from string literals (`N'INSERT …' + N'SELECT …'`) is one known command and is parsed. A
  command that involves a variable, a function or anything else is counted in `unparsedSteps`. A variable assigned
  under `IF` / `ELSE` holds one of several values afterwards, so it is unknown too. A `CASE … ELSE … END` is an
  expression wherever it appears (`SET`, `DECLARE`, `SELECT`): its `ELSE` and `END` are not control flow.
- `sp_update_job` applies every setting `sp_add_job` takes, in script order: SSMS's *Script Job as → CREATE* sets the
  start step that way (`@start_step_id`), after the steps exist.
- `sp_delete_jobstep` renumbers the later steps and updates the jumps to them (Microsoft documents that deleting a step
  updates the steps that reference it); what a jump to the deleted step becomes is not documented, so it is shown as
  a quit, with a coverage warning.
- Files are named by their path below the selected folder (`prod/jobs.sql`), so `dev/jobs.sql` and `prod/jobs.sql`
  are two files. A script that cannot be analysed is a coverage warning (`could not be analysed completely`); the
  other files are still documented. Load errors in a document carry the file name and a generic reason only; the
  command line prints the full reason to its own console, for a script that could not be analysed with the
  exception type and message.
- T-SQL object names keep their own database: `Sales.dbo.FactSales` is database `Sales` whatever the step's
  `@database_name`; `Sales..FactSales` is database `Sales` with the default schema left out; a four-part name records
  its linked server.

## Layout

```
sqlagentdocgen/
  tokenizer.py      string/comment-aware T-SQL tokenizer, GO batch splitter
  script_parser.py  EXEC call extraction (named/positional/OUTPUT/conditional)
  registry.py       job/step/schedule correlation and identity
  xmla.py           XMLA <Batch> analysis
  tmsl.py           TMSL JSON analysis
  schedules.py      msdb frequency model -> descriptions
  processing.py     SSAS processing sequence across jobs (per target)
  execution_graph.py  step flow (success/failure goto) per job
  analyzer.py       payload orchestration + normalized entity model
  issues.py         deterministic review findings
  redact.py         payload secret scrubbing (safety net for all renderers)
  renderer.py + template.html   self-contained interactive HTML
  word_writer.py    narrative Word document (--also word)
  agent_writer.py   agent context markdown (--also agent)
  loader.py         .sql discovery and 64 KB sp_add_job sniffing
  cli.py            command line (--coverage-only, --html, --also, CSV)
  details.py        native payload constants
generate_docs.py    folder -> docs wrapper
tests/              synthetic and adversarial fixtures
examples/           small synthetic example scripts
```
