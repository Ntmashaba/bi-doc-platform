# Windows validation pack

Status: **prepared, not yet run on a Windows machine.** Nothing here has been executed with a real `pbi-tools.exe` or on a
clean Windows install. CI (hosted `windows-latest`, which has Python on disk and no Power BI Desktop) covers the
installer and the portable reader only.

Three questions, each open until its evidence is captured:

| # | Question | Needs |
|---|---|---|
| V1 | Does the installer work on a clean Windows machine with no Python? | A clean Windows 10/11 machine or VM (**owner**) |
| V2 | Does a real pbi-tools + Power BI Desktop extract and document a PBIX through the platform? | Windows with Power BI Desktop and pbi-tools Desktop (**owner**) |
| V3 | Do pbi-tools and the portable pbixray reader agree on the same file? | Same machine as V2 (**owner**) |

What this pack can show is limited to the files listed. A pass is evidence for those files, not a claim of general parity
or production readiness. A difference is a finding to read, not automatically a defect in either extractor.

## Public inputs (checksum-pinned)

All are in `samples/manifest.json`; `python scripts/fetch_samples.py [id ...]` downloads them into `samples/downloads/`
and verifies each SHA-256 (it refuses a mismatch).

| Use | Manifest id | Checksum | Licence / provenance |
|---|---|---|---|
| V2/V3: Import + DirectQuery, SQL Server | `DP500 04 DirectQuery SQL Server` | in manifest | Microsoft public training sample |
| V2/V3: composite model | `DP500 08 Composite model` | in manifest | Microsoft public training sample |
| V2/V3: Dual storage | `DP500 11 Dual storage mode` | in manifest | Microsoft public training sample |
| V2/V3: import model | `AdventureWorks Sales` | in manifest | Microsoft public training sample |
| V2/V3: import model | `Adventure Works, Internet Sales` | in manifest | PBIXRay upstream test fixture |
| V1: ABF backup | `adventure-works-tabular-model-1200-full-database-backup` | in manifest | Microsoft `sql-server-samples`, MIT licence, 2.1 MB |
| V1: thin report (live connection) | `live-connection-ssas`, `live-connection-pbiservice` | in manifest | PBIXRay upstream test fixture |

Hashes are in `samples/manifest.json` and enforced by the fetch script.

Known limit: there is **no public matched thin-report/ABF pair**. The pairing demonstration in the acceptance gallery is
synthetic and labelled so. This pack does not attempt to close that gap.

## V1. Clean-machine installation

Preparation is kept apart from execution, so that nothing on the target machine needs Python.

**Prepare (any machine with PowerShell; Python is not used).** Copy the results to the target machine.

1. Get a checkout of this repository at the commit under test (for the script, its inputs and `samples\manifest.json`).
2. Get both installers and `SHA256SUMS.txt` from the `bidoc-windows-installer` artifact of a CI run on that commit
   (`bidoc-setup-0.2.0-unsigned.exe`, the version that is installed first, and `bidoc-setup-0.2.1-unsigned.exe`, the
   upgrade). A CI run publishes both only from the commit that carries the "Publish both installers" step.
3. Download the ABF sample and check it against the pinned checksum, with PowerShell only:

```powershell
$item = (Get-Content samples\manifest.json -Raw | ConvertFrom-Json) | Where-Object { $_.id -eq "adventure-works-tabular-model-1200-full-database-backup" }
New-Item -ItemType Directory -Force samples\downloads | Out-Null
Invoke-WebRequest $item.url -OutFile "samples\downloads\$($item.file)"
if ((Get-FileHash "samples\downloads\$($item.file)" -Algorithm SHA256).Hash.ToLower() -ne $item.sha256) { throw "checksum mismatch" }
$item.sha256        # keep this value; it is passed to the script below
```

**Execute (the clean target machine).** Windows 10/11 with no Python, no Docker, no Power BI Desktop needed. Use a fresh VM
snapshot or a new user account on a machine without Python. The script is run with PowerShell 7 (`pwsh`), which CI uses;
if it is not installed, installing it (`winget install Microsoft.PowerShell`) is the one tool added to the machine, and it
should be written down in the evidence.

```powershell
# verify the installers against SHA256SUMS.txt (compare each hash with its line in the file)
Get-FileHash .\bidoc-setup-0.2.0-unsigned.exe, .\bidoc-setup-0.2.1-unsigned.exe -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
# run the acceptance script from a normal (non-elevated) pwsh; the checksum is read with PowerShell, not Python
$abfSha = ((Get-Content samples\manifest.json -Raw | ConvertFrom-Json) | Where-Object { $_.id -like "*full-database-backup" }).sha256
pwsh packaging\windows\verify-install.ps1 `
  -Installer .\bidoc-setup-0.2.0-unsigned.exe -UpgradeInstaller .\bidoc-setup-0.2.1-unsigned.exe `
  -AbfArchive samples\downloads\adventure-works-tabular-model-1200-full-database-backup.zip -AbfSha256 $abfSha `
  -Report verify-report.json
```

Expected: exit code 0; `verify-report.json` has `"passed": true`, every check `ok`, and
`machine.clean_machine: true` (no Python on disk). Checks include install/registration, `doctor` (PBIP, ADF, PBIX and ABF
ready without pbi-tools), batch generation, PBIX and ABF generation with the bundled reader, the desktop app on
127.0.0.1 only, upgrade keeping history and settings, and uninstall.

Evidence to capture: `verify-report.json`; the console transcript; `%TEMP%\bidoc-verify-*\install.log` and `upgrade.log`;
Windows version (`winver`); `Get-FileHash` outputs; a screenshot of the desktop window opening (`BI Documentation
Generator.exe`); the SmartScreen prompt, if any (the build is unsigned, so a prompt is expected and is not a failure).

Not covered by the script (record by hand): whether the desktop window renders with the machine's WebView2 runtime.

## V2. Real pbi-tools execution (owner's machine)

Prerequisites: Windows, Power BI Desktop installed, pbi-tools **Desktop** (not `pbi-tools.core`) from https://pbi.tools
(AGPL-3.0; installed separately, never bundled). Record the version (`pbi-tools.exe info`) and Power BI Desktop version.

```powershell
$tools = "C:\path\to\pbi-tools.exe"
bidoc config --pbi-tools $tools
bidoc doctor --json | Out-File -Encoding utf8 doctor.json     # not `>`: Windows PowerShell 5.1 would write UTF-16
bidoc doctor | Out-File -Encoding utf8 doctor.txt
```
Use `Out-File -Encoding utf8` (or `Tee-Object` into a file you then re-save as UTF-8) for every captured output below: in Windows
PowerShell 5.1 a plain `>` writes UTF-16, and a console that reads UTF-8 as ANSI turns arrows such as `↔` into `â†”`.
Expected: `doctor.json` -> `pbix_backend.backend = "pbi-tools"`, `fallback = null`, `inputs.pbix.available = true`, and the
`pbi-tools` check `ok`. If it says `fallback` or names a `pbi_tools_problem`, capture it: that is the launch probe finding a
problem, and the finding is the result.

```powershell
foreach ($id in "DP500 04 DirectQuery SQL Server","DP500 08 Composite model","DP500 11 Dual storage mode","AdventureWorks Sales") {
  bidoc generate --engine power_bi --kind pbix --source "samples\downloads\$id.pbix" --pbi-tools $tools --output-dir "out\pbi-tools\$id"
}
```
Expected: each exits 0, writes one `.html`, and the progress line on stderr reads `extracting (pbi-tools)` (an explicit
`--pbi-tools` is never replaced by the portable reader; the `PBIX extractor:` summary line belongs to `bidoc batch`, not `generate`). A per-file extraction failure is reported as a failure and is **not** retried with pbixray; record it with
`pbi-tools.log` from the workspace.

Also try two negative cases. Both exit with code **3**. The internal error code `PREREQUISITE_MISSING` is not printed; the output is:
- `--backend pbi-tools --pbi-tools C:\nope\pbi-tools.exe`:
  `error: pbi-tools cannot be used: pbi-tools was not found at C:\nope\pbi-tools.exe. PBIP, model and ADF inputs still work.`
  (it states the problem and does not name a fix);
- a copy of `pbi-tools.exe` renamed to `pbi-tools.bat`:
  `error: pbi-tools cannot be used: configure the pbi-tools executable, not a script wrapper. ...`

Evidence: `doctor.json`, each command's stdout/stderr and exit code, the `.html` files, `pbi-tools.log`, tool and Desktop
versions.

## V3. Same-file comparison against PBIXRay (owner's machine)

`scripts/compare_extractors.py` extracts the same file with both readers, loads both through the platform loader and
compares tables, columns (type, hidden, expression, sort-by, format), measures (DAX, format, folder), relationships,
roles, shared expressions, per-partition storage mode (import / directQuery / dual) and definitions, and per-partition
sources. It needs the platform requirements installed (`pip install -r
requirements.txt` in a checkout) and works from a checkout, not from the installed app.

```powershell
foreach ($id in "DP500 04 DirectQuery SQL Server","DP500 08 Composite model","DP500 11 Dual storage mode","AdventureWorks Sales","Adventure Works, Internet Sales") {
  python scripts\compare_extractors.py "samples\downloads\$id.pbix" --pbi-tools $tools --report "compare\$id.json"
}
```
Exit codes: 0 no differences, 1 differences (see the report), 2 one side could not run or the report cannot be written (the
report's folder is created if missing; say which).

Expected: possibly non-zero. The baseline run (`docs/validation/results/2026-10-01-windows-baseline/`) found 29 differences, all
`crossFilteringBehavior` spelling. After the cross-filter normalisation the rerun
(`docs/validation/results/2026-10-01-windows-rerun/`) found none, so zero differences is now the expected result for those five
files. Differences worth recording, not assuming away:
- expressions that differ only in whitespace (`whitespace_only: true`);
- connection strings: pbi-tools reads them from the Mashup, pbixray may redact or omit them, so `sources` can differ;
- objects present in one extract only.
Each difference must be classified in the write-up as a reader defect, an expected representation difference, or unknown.

Evidence: every `compare\*.json`, the console summary lines, tool versions, and for each non-empty difference the raw
extract folders from both readers (run `pbi-tools.exe extract FILE -extractFolder DIR -modelSerialization Raw` and
`python -m pbidocgen.portable FILE DIR2` by hand) so the difference can be reproduced.

## Results so far

- 2026-10-01 baseline (`docs/validation/results/2026-10-01-windows-baseline/REPORT.md`, kept unmodified): V2 passed on one
  machine; V3 found 29 differences, all one enum spelling; V1 not run.
- 2026-10-01 rerun after the cross-filter normalisation (`docs/validation/results/2026-10-01-windows-rerun/`): all five comparisons
  found **0 differences** (baseline: 4, 5, 5, 8 and 7). The two readers agree on every compared fact for those five files; that is
  not a claim of general parity.
- Not covered by any run: **row-level-security parity** (all five samples have zero roles), clean-machine installation, live SSAS
  scanning, a genuine matched thin-report/ABF pair.

## What a full pass would and would not establish

- V1 + V2 + V3 passing: the installer works on one clean machine; pbi-tools is launched correctly by the platform on one
  machine with one Power BI Desktop version; the two readers agree on five public files.
- Not established: other Power BI Desktop / pbi-tools versions, encrypted or Mashup-heavy models, large files, corporate
  inputs, signed-installer behaviour, and any matched thin-report/ABF pairing.
