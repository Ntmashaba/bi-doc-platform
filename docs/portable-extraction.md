# Portable PBIX extraction

## What it is

An opt-in way to document a PBIX file without pbi-tools, on any operating system: `bidoc generate --kind pbix
--pbixray` (or `pbi-doc-gen --pbix FILE --pbixray`). pbi-tools stays the default and the authoritative route; nothing
changes for a user who does not ask for this.

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

## Entry points

| Workflow | Behaviour |
|---|---|
| `bidoc generate --kind pbix` | pbi-tools, as before |
| `bidoc generate --kind pbix --pbixray` | Portable reader |
| `pbi-doc-gen --pbix FILE --pbixray` | Portable reader (same flag on the engine's own command line) |
| `bidoc doctor` | Reports the installed pbixray and the supported range |
| Batch, desktop, worker | pbi-tools only, as before |

Install with `pip install "pbi-doc-gen[portable]"` (`requirements.txt` already does).

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
- Windows behaviour (this reader in the frozen build) has not been validated.
