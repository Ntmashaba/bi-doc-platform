# Windows validation report (V1, V2, V3)

Commit under test: b96fb0a91f7e0dd10136ca844b39b326d14c01fc (main). Run 2026-10-01. Nothing was committed or edited in the repo.

## Machine and tools
- Windows 11 Pro Insider Preview 10.0.26220; Windows PowerShell 5.1.26100.9568 (`pwsh` NOT installed)
- Python 3.13.1 (system); venv at `C:\validation-results\venv` with `requirements.txt` installed; `bidoc 0.2.0`
- Power BI Desktop 2.158.1177.0 (Microsoft Store); pbi-tools Desktop 1.2.0 (build 1.2.25006.2036, .NET Framework 4.8), installed separately at `D:\GIT\bi-doc-platform\pbi-tools\` (git-ignored, not bundled)
- pbixray 0.15.5, pbi-doc-gen 0.4.1, adf-doc-gen 0.4.0, WebView2 155.0.4283.24
- Python AND Docker are present on this machine, so it can never be a clean-machine run. Files: machine.txt, versions.txt

## Results
| Check | Result | Evidence |
|---|---|---|
| V1 clean-machine install | **NOT RUN** | see below |
| V2 doctor | PASSED | doctor.json, doctor.txt (backend pbi-tools, fallback null, pbi_tools_problem null, pbix available, all checks ok) |
| V2 generate x4 (explicit --pbi-tools) | PASSED, 4/4 exit 0, one .html each, stderr shows `extracting (pbi-tools)` | generate-*.txt, out\pbi-tools\ |
| V2 negative: wrong path | Non-zero (exit 3) as expected; wording differs from the doc, see below | neg1.txt |
| V2 negative: .bat wrapper | Non-zero (exit 3), message as expected | neg2.txt |
| V3 compare x5 | exit 1 on all five (differences found, no crash, no exit 2) | compare\*.json / *.txt |

### V1: NOT RUN
Blocked, not attempted: (a) the installers need the GitHub Actions artifact (login required) and are not on this machine, (b) `pwsh` is missing and installing it via winget adds software, (c) the machine has Python/Docker, so `machine.clean_machine` would be false. The owner chose to also run a non-clean V1; this needs the two installers plus permission to install PowerShell 7.

### V2 details
- doctor: `explicit` is false because the path came from stored config (`bidoc config` already pointed at the same exe; config unchanged).
- Per file exit 0: DP500 04 DirectQuery, DP500 08 Composite, DP500 11 Dual, AdventureWorks Sales. Platform warnings printed (no date dimension; many-to-many; inactive relationships; `Sales Order <-> Sales` bidirectional) are model findings, not errors. The `â†”` glyphs seen in the saved text files are a console/redirect code-page artifact of reading UTF-8 as ANSI.
- Wrong path (neg1): `error: pbi-tools cannot be used: pbi-tools was not found at C:\nope\pbi-tools.exe. PBIP, model and ADF inputs still work.` exit 3. Doc expects a `PREREQUISITE_MISSING` error "naming the fix": that token does not appear in the output, and the message states the problem but not a fix. Documentation/message mismatch, not verified further.
- .bat copy (neg2): `error: pbi-tools cannot be used: configure the pbi-tools executable, not a script wrapper. ...` exit 3.
- No `pbi-tools.log` was produced because no extraction failed.

### V3 table (pbi-tools vs pbixray; counts identical on both sides in every category)
| File | Exit | Total differences | Categories with differences |
|---|---|---|---|
| DP500 04 DirectQuery SQL Server | 1 | 4 | relationships 4 of 4 |
| DP500 08 Composite model | 1 | 5 | relationships 5 of 5 |
| DP500 11 Dual storage mode | 1 | 5 | relationships 5 of 5 |
| AdventureWorks Sales | 1 | 8 | relationships 8 of 9 |
| Adventure Works, Internet Sales | 1 | 7 | relationships 7 of 7 |

Tables, columns, partitions (incl. storage mode), measures, roles, expressions and sources: no differences in any file. No whitespace-only or connection-string differences occurred.

All 29 differences are the same one: `crossFilteringBehavior` is `singleDirection` on the pbi-tools path and `oneDirection` on the pbixray path; `isActive` and cardinalities agree. Classification: **expected representation difference**. Evidence from the raw extracts (raw\): pbi-tools' Raw `database.json` writes the property only when it is not the default (only the one `bothDirections` relation carries it), and the platform fills the default as `"singleDirection"` (`model_parser.py:379`, `tmdl_reader.py:472`), whereas the portable reader maps its numeric value to `"oneDirection"` (`portable.py:246`). The one bidirectional relationship in AdventureWorks Sales (`Sales Order <-> Sales`) is the 9th relationship and agreed on both sides (`bothDirections`). Whether `singleDirection` vs `oneDirection` could affect rendering downstream was not checked.

Raw extracts for reproduction: `raw\pbi-tools\<file>` and `raw\pbixray\<file>` (all exit 0).

## Unexpected / not matching the document
- The doc's V2 expectation of a `PREREQUISITE_MISSING` message and a named fix does not match the observed wrong-path message.
- Windows PowerShell 5.1 `>` writes UTF-16; the handover's `Out-File -Encoding utf8` avoids that (the repo doc's `> doctor.json` does not).
- The comparator reports 29 differences that are one enum spelling; it does not normalize it.

## What this does not show
Other Power BI Desktop / pbi-tools versions; encrypted or heavily Mashup-driven models; large files; corporate inputs; signed-installer behaviour; any clean-machine install (V1 not run); a matched thin-report/ABF pair (none is public; the repo's pairing demonstration is synthetic and labelled so). The V3 result covers five public files, one machine, and one pbi-tools/Desktop version.
