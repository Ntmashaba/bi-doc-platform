# B01 spikes

Throwaway feasibility scripts. They are not product code. They expect `pbi-doc-gen` and `adf-doc-gen` checked out beside this repository (or set `PBI_DOC_GEN` / `ADF_DOC_GEN`).

| Script | What it shows |
|---|---|
| `probe_pbi_secrets.py` | Seeded secrets/entered data in M expressions reach Power BI HTML and JSON (exits 1 while any leak). |
| `probe_adf.py` | ADF redaction coverage, SQL port/case merging, path case, Delete-as-sink. Prints a JSON report. |
| `extract_native.py FILE.html...` | Recovers the native payload from existing engine HTML without executing it; prints sizes. |

Results are recorded in `docs/b01/BASELINE.md`.
