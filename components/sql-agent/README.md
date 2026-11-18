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
python -m sqlagentdocgen path/to/jobs/ -o payload.json   # full payload
python -m sqlagentdocgen path/to/jobs/ --coverage-only   # shareable counts only
```

`--coverage-only` prints only aggregate counts and warning texts — never job
names, command text, or file content — so real scripts can be validated
without anything sensitive leaving your machine.

## What it understands

- `sp_add_job`, `sp_add_jobstep` (TSQL, SSIS, ANALYSISCOMMAND, ANALYSISQUERY,
  CmdExec, PowerShell subsystems), `sp_add_jobschedule`,
  `sp_add_schedule` + `sp_attach_schedule` (shared schedules),
  `sp_detach_schedule`, `sp_add_jobserver` (MSX/TSX registration),
  `sp_update_job` (final enabled state), `IF EXISTS ... sp_delete_job`
  recreate preambles.
- XMLA `<Batch>`: Transaction/ProcessAffectedObjects attributes, `<Parallel>`,
  `<ErrorConfiguration>`, Process (all types; ProcessAdd out-of-line Bindings
  count as reads), Backup, Restore, Alter, Create, Delete, Sync, Detach,
  Attach, ClearCache.
- TMSL JSON: refresh (type + database/table/partition objects),
  createOrReplace, alter, delete, backup, restore, sequence envelopes.
- T-SQL step bodies via `adf-doc-gen`'s token-based harvester (tables read /
  written, procedures executed — never delete).

## Layout

```
sqlagentdocgen/
  tokenizer.py      string/comment-aware T-SQL tokenizer, GO batch splitter
  script_parser.py  EXEC call extraction (named/positional/OUTPUT/conditional)
  registry.py       job/step/schedule correlation and identity
  xmla.py           XMLA <Batch> analysis
  tmsl.py           TMSL JSON analysis
  schedules.py      msdb frequency model -> descriptions
  analyzer.py       payload orchestration
  issues.py         deterministic review findings
  loader.py         .sql discovery and 64 KB sp_add_job sniffing
  cli.py            command line (--coverage-only)
  details.py        native payload constants
generate_docs.py    folder -> JSON payload wrapper
tests/              synthetic and adversarial fixtures
examples/           small synthetic example scripts
```
