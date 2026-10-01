# Windows validation baseline, 2026-10-01

`REPORT.md` is the validation agent's report, kept **unmodified** as the baseline. Later runs are compared against it and do
not replace it; add each new run in its own dated folder next to this one.

What it covers: commit `b96fb0a` (`main` after #31 and #32); Windows 11 Pro Insider Preview 10.0.26220; Power BI Desktop
2.158.1177.0 (Microsoft Store); pbi-tools Desktop 1.2.0; pbixray 0.15.5. The machine had Python and Docker, so it was never a
clean-machine run.

| Check | Outcome in the baseline |
|---|---|
| V1 clean-machine installation | **Not run** (installers not on the machine, `pwsh` missing, machine not clean) |
| V2 real pbi-tools: `doctor` and four generations | Passed (explicit `--pbi-tools`; stderr `extracting (pbi-tools)`); two negative cases exit 3 |
| V3 same-file comparison, five files | Exit 1 on all five: 29 differences, all `crossFilteringBehavior` spelling (`singleDirection` vs `oneDirection`) |

Why the 29 differences were not extraction errors, per the report's analysis of the raw extracts: both sides agree on every relationship's direction (only
`AdventureWorks Sales` has a bidirectional one, and both sides agree on it). The platform's own default spelling
(`singleDirection`) differed from the portable reader's (`oneDirection`), and every renderer treats both as single direction.
The shared model now uses one canonical spelling (see the PR that follows this baseline).

**Not in the repository:** the raw comparison JSON reports and extract folders (`compare\*.json`, `raw\`) stayed on the validation
machine. Keep them; this baseline cites them but does not contain them.

**Limits that still stand**
- All five samples have **zero roles**, so row-level-security parity between the two readers is untested.
- One machine, one pbi-tools version and one Power BI Desktop version, five public files.
- Outstanding: clean-machine installation, live SSAS scanning, and a genuine matched thin-report/ABF pair (none is public; the
  pairing demonstration in the repository is synthetic and labelled so).
- The rerun of the five comparisons after the cross-filter normalisation has **not** been done; unit tests do not stand in for it.
