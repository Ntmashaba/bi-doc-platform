# Windows rerun, 2026-10-01 (after the cross-filter normalisation)

The same five same-file comparisons as the [baseline](../2026-10-01-windows-baseline/REPORT.md), rerun on the same Windows
machine after the cross-filter normalisation was merged (PR #33, merge `e7150a9`). The baseline is unchanged and stays the
point of comparison.

| File | Baseline differences | Rerun differences | Exit |
|---|---|---|---|
| DP500 04 DirectQuery SQL Server | 4 | 0 | 0 |
| DP500 08 Composite model | 5 | 0 | 0 |
| DP500 11 Dual storage mode | 5 | 0 | 0 |
| AdventureWorks Sales | 8 | 0 | 0 |
| Adventure Works, Internet Sales | 7 | 0 | 0 |

For each file the two readers agree on every compared fact: tables, columns, partitions (including storage mode), measures,
relationships (4, 5, 5, 9 and 7 of them), roles, shared expressions and per-partition sources. The 29 baseline differences were
all one enum spelling for relationship cross-filter direction; after the fix both readers give the same canonical value.
"Zero differences" means these two readers agree on these facts for these five files. It is not a claim of general parity.

## How it was run, and what is in this folder

- Commit `a7421d4` (contains the fix merge `e7150a9`); pbi-tools Desktop 1.2.0 (build 1.2.25006.2036) and Power BI Desktop
  2.158.1177.0, the same versions as the baseline. The pbi-tools used was downloaded again from the 1.2.0 release (zip SHA-256
  `BCA88ABB9F30D17EE354A06E75EB3F6C6BDEEB9C98A46990E86978A52A3876B7`); the untracked `pbi-tools/` folder in `versions.txt` is that
  download and is not part of the repository.
- The comparison itself is `scripts/compare_extractors.py`, run once per file. The validation agent's own procedure produced
  the summary and `versions.txt`; the `rerun-comparisons.ps1` helper written for this rerun was **not** what produced them.
- `summary.md` and `versions.txt` as received, and `compare/` holding each file's console output and JSON report. The raw
  extract folders were not kept because no difference was found.
- One edit: the Windows user-profile name in one path in `versions.txt` (`C:\Users\<user>\...`) was replaced with `<user>`
  before publishing. Nothing else was changed.

## Limits that still stand

- **Row-level-security parity is untested**: all five samples have zero roles.
- One machine, one pbi-tools version, one Power BI Desktop version, five public files. Other versions, encrypted or heavily
  Mashup-driven models, large files and corporate inputs are not covered.
- Not done: a clean-machine installation (V1). Live Analysis Services scanning is not implemented. There is no genuine matched
  thin-report and Tabular-backup pair; the repository's pairing demonstration is synthetic and labelled so.
