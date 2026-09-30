# Portable PBIX extraction

## What it is

A way to document a PBIX file without pbi-tools, on any operating system. pbi-tools stays the preferred and
authoritative route: the portable reader is used when you ask for it (`--backend pbixray`), or when no usable
pbi-tools is available. It never replaces a pbi-tools that can run.

The reader is `pbidocgen/portable.py`, built on pbixray, and runs in the same child process as pbi-tools extraction,
so the timeout and cancellation are unchanged. No Analysis Services server is started and no business-table rows are
decoded. A thin (live-connection) report is documented from its report and connection metadata, without pretending a
local model exists.

The public pbixray DataFrames omit fields the documentation engine needs, so `portable.py` holds one isolated
bridge, limited to a validated pbixray range, to the decompressed `metadata.sqlitedb`. It checks the required schema,
queries SQLite read-only, and maps measures, columns, relationships, partitions, hierarchies, expressions, roles and
sources into the existing model parser. An unsupported schema fails explicitly instead of returning an empty model.
This is a documentation representation, not a round-trip BIM export.

Measure format strings, hidden flags, descriptions, display folders, lineage tags and column sort references are kept
where present. The HTML shows storage modes, extraction provenance and coverage warnings; anything the reader could
not read marks the document partial, which keeps it local-only and suppresses deletion recommendations.

## Which extractor is used, and where

One policy decides it for every entry point (`apps/generator/bidoc_generator/backend.py`, using the launch and
prerequisite checks in `pbidocgen/pbi_tools_runtime.py`, which the engine's own command line shares). The same machine
and configuration give the same answer in `bidoc generate`, `bidoc batch`, the desktop app, the worker and `bidoc doctor`.

An **explicit** instruction (`--pbi-tools EXE`, `--backend pbi-tools`, `--backend pbixray`) is never overridden. A
pbi-tools that was only **configured** (`BIDOC_PBI_TOOLS`, `config.json`) or found on `PATH` is used when it can run and
replaced, with a printed reason, when it cannot.

| Situation | Result |
|---|---|
| Auto; a usable pbi-tools is configured | pbi-tools |
| Auto; no pbi-tools configured | The portable reader (if its pbixray version is supported) |
| Auto; the configured pbi-tools cannot run here (not Windows, no Power BI Desktop, not launchable, missing file) | The portable reader, with a note giving the reason |
| `--pbi-tools EXE` or `--backend pbi-tools` that cannot be used | An error with the reason; never a silent switch |
| `--backend pbixray` (alias `--pbixray`) | The portable reader, or the reason it is unavailable, whatever else is configured |
| Neither usable | PBIX reported unavailable, with both reasons, the same way everywhere |
| ABF input | Always the portable reader; `--pbi-tools` or `--backend pbi-tools` is rejected |
| `--pbixray` together with `--pbi-tools` | Rejected: they are alternatives |
| `pbi-doc-gen --pbix FILE` (the engine's own command line) | The same rules, using pbi-tools from `PATH` as the implicit one |

"Can run" is checked in three steps: the file exists and is not a script wrapper or `pbi-tools.core`; this machine can run
pbi-tools Desktop at all (Windows with Power BI Desktop); and the file starts and exits within 15 seconds. The last step
shows the file is a launchable program on this platform. It is not a functional test. The probe runs the file as the
leader of its own process group; if it does not finish in time, or the caller is interrupted, the **whole process tree** is
ended (POSIX: SIGKILL to the group; Windows: `taskkill /T /F`), bounded to 10 s, and an interrupted probe is not cached. A
child that outlives a probe that already exited normally is not chased.

**A fallback is reported, once.** When PBIX items in a batch use the portable reader because the configured pbi-tools
cannot be used, the reason is printed once on stderr, shown as one `PBIX extractor:` line in the normal batch output,
recorded once in the batch's structured record (`options.pbix_backend` in `bidoc batch --json`, `bidoc history --json` and
the desktop API's batch and `/api/state` responses), and shown as one notice above the desktop app's batch table. It is
not repeated per file, and the same record says when neither backend is usable.

**Readiness is not extraction success.** The worker advertises PBIX, and `bidoc doctor` and the desktop app report it, when
the selected backend is present and can start; a file being present at the configured path is not enough. Whether one
particular PBIX extracts is known only when it is extracted. A failed extraction (including a program that cannot be
started, which is reported as a structured `PREREQUISITE_MISSING` error rather than an exception) is reported for that
file and is never retried through the other backend.

| Other entry points | |
|---|---|
| `bidoc doctor` | Reports pbixray, the pbi-tools it would look at and why it is unusable, and the selected backend |
| `bidoc batch` and the desktop file picker | `.abf` files are recognised and use the portable reader |
| Hosted upload UI | Unchanged (no ABF upload) |

Install with `pip install "pbi-doc-gen[portable]"` (`requirements.txt` already does).

## What a document can and cannot say

Each of these is a different claim, and the documents keep them apart.

- **Thin (live-connection) report detection** identifies the remote connection (server, database, model or workspace, as far
  as the file records it) and what the report requires: its pages, visuals and field references. It does not retrieve the
  remote model's metadata. The HTML keeps its warning that, without a model, field references are unresolved
  requirements: the document cannot confirm the fields exist, tell measures from columns or trace them to a source.
- **Remote semantic-model metadata is not retrieved automatically.** Nothing connects to a server, workspace or service. To
  document the model you must supply it (a `.bim`, TMDL, an ABF backup or a PBIX with the model embedded).
- **Explicit model pairing** (`--model`) resolves the report's fields against the snapshot you supplied. It does not verify
  that the snapshot is the server's model, or that it is current: server identity and freshness stay unverified, and the
  document says so. A report that already has its own local or composite model is rejected rather than having it replaced.
- **DirectQuery lineage depends on the input representation and the partition type.** From a PBIP, TMDL or BIM model,
  a table whose partition reads a remote Analysis Services or Power BI model (an entity partition with an
  `expressionSource`) is traced to that remote model. From an embedded PBIX model or an ABF backup, the portable reader maps
  query, M and calculated partitions and calculation groups; any other partition type is recorded with an unknown source,
  the extraction notes that remote-model lineage is incomplete, and the document is marked partial. Storage mode
  (Import, DirectQuery, Dual) is read for every table.
- **Still incomplete where documented:** embedded entity partitions in remote-model PBIX files, advanced calculation-group
  semantics (extracted, but selection and format semantics are not verified) and object-level security. A detected gap
  marks the document partial, which keeps it local-only.

## Input limits (untrusted files)

pbixray decompresses the whole data model, including the compressed table data, into a temporary file before
anything is parsed. A few KiB of crafted input can claim hundreds of GiB. `pbidocgen/input_limits.py` bounds this:

- **Decompressed size:** at most the larger of 256 MiB and 50x the input size, never above 16 GiB or 90% of the
  free space in the temp folder. Real files decompress to about 2-3x their size. Set
  `BIDOC_MAX_DECOMPRESSED_BYTES` to raise the limit for a file you trust. A refused file leaves no temp file
  behind (stock pbixray leaves the partial file). If the private pbixray hook needed to enforce the limit is
  missing, extraction refuses instead of running unbounded.
- **Metadata database:** at most 512 MiB. It is opened in memory, refused unless it holds only plain tables and
  indexes (no views, triggers or virtual tables), with `trusted_schema` off, `query_only` on and an authorizer
  that permits only `SELECT` and `PRAGMA table_info`.
- Report layout and PBIR files were already size-capped by their declared sizes, which Python's `zipfile`
  cannot be tricked into exceeding.
- Extraction still runs in the child process with its timeout and cancellation. These limits bound disk use;
  the timeout bounds time.

## Keeping pbixray current

Portable extraction reaches into private pbixray names, so it accepts only a validated range of pbixray versions
(`portable.SUPPORTED`, currently `>=0.15.0,<0.16`). `portable.PRIVATE_TOUCHPOINTS` lists each private name and the
code that uses it. Pre-releases and dev builds are never accepted. Three things watch it:

- **Every build:** `components/power-bi/tests/test_pbixray_contract.py` runs the canary checks against the
  installed version and fails if a private name, its shape, the metadata schema, the output for a real composite
  model or the decompression size cap no longer behaves as validated. It also fails if the range differs between
  `portable.py` and both `pyproject.toml` extras, if `requirements-lock.txt` pins a version outside it, or if any
  other source file hard-codes the range.
- **Every week:** `.github/workflows/pbixray-canary.yml` runs `tests/tools/pbixray_canary.py` against the newest
  pbixray on Linux and Windows. It is not a merge gate; a red run is the alert. Run it by hand for a specific
  version with the workflow's `version` input.
- **For users:** `bidoc doctor` and the errors name the installed version, the supported range and what to do,
  instead of saying only that pbixray is missing.

**Why a range:** pbixray 0.15.0, 0.15.1, 0.15.2, 0.15.3, 0.15.4 and 0.15.5 each pass the canary and produce
byte-identical documents for all 121 public PBIX models tried, so later 0.15.x patch releases are accepted. The
lock file still pins one exact version for reproducible builds.

To widen the range (for example to 0.16): install the candidate, run the canary, and compare full output on a
corpus (the acceptance samples and any models you can share). If the canary fails, fix `portable.py` /
`input_limits.py` (and `GOLDEN` in the canary only if the output changed on purpose). Then change `SUPPORTED`,
`SUPPORTED_MIN` and `SUPPORTED_BELOW` in `portable.py`, the two `pyproject.toml` extras and, if needed, the lock
pin; the contract test confirms they agree.

## Deliberate limits

- An approximation of a pbi-tools extract: no live server inventory, refresh, remote model retrieval or backup
  freshness check.
- Entity partitions in embedded remote-model PBIX, advanced calculation-group semantics and object-level security are
  not fully documented. Detected gaps mark the document partial and prevent complete publication claims.
- Dependency parity is not established. All portable outputs suppress deletion recommendations even where metadata
  extraction is complete.
- Partial shared output still applies projection and withholds query code by default; it remains local-only.
  Projection is best-effort, not a guarantee of anonymisation.
- No same-file comparison against a real pbi-tools extract has been run.
- Windows: the frozen executables bundle the reader (pbixray, its metadata and modules) and start the extraction by
  re-invoking themselves with `--portable-extract`. That path is checked on Linux with a PyInstaller build run with no
  Python on `PATH`, and on a hosted Windows runner by `packaging/windows/verify-install.ps1` (install, `doctor`, PBIX
  generation with the bundled reader, upgrade, uninstall). A hosted runner still has Python on disk, so a **clean
  Windows machine** run of the same script (`docs/b09/VERIFICATION.md`) remains the open gate. ABF has no small fixture
  in that script and is checked only in the Linux frozen build.
