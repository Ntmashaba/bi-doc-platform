# Engine components

`power-bi`, `adf` and `sql-agent` are the documentation engines. `power-bi` and `adf` were previously the standalone
repositories `Ntmashaba/pbi-doc-gen` and `Ntmashaba/adf-doc-gen`; `sql-agent` was written in this repository (see below).

| Component | Imported from | Commit |
|---|---|---|
| `power-bi` | `Ntmashaba/pbi-doc-gen` | `0b04cf41d1cff0e4d7fc3587fff6e14f86fd405f` |
| `adf` | `Ntmashaba/adf-doc-gen` | `960b4bc165efff415d1adb4932fb39196fc9eff1` |

The trees are copies of those commits with these deliberate differences:

- The CLI moved from the root `generate_docs.py` into the package (`pbidocgen/cli.py`, `adfdocgen/cli.py`) so the
  wheel contains it; `generate_docs.py` remains as a thin compatibility shim, and the console-script entry point and
  packaging test follow the move. The moved code is otherwise unchanged.
- `power-bi/pbi-tools/` (Windows binaries) and `power-bi/pbix-samples/` (sample `.pbix` files, about 74 MB) are not
  imported. Tests that read them are unchanged from the standalone repository.
- Each `pyproject.toml` declares `license = { text = "MIT" }`, matching the repository's [LICENSE](../LICENSE), added
  after the import.

To re-check the copies against the original repositories (each checked out at the commit above), run this at the
import commit (`63d210c`); later changes to `components/` are in git history and are described in `docs/PROGRESS.md`:

    python scripts/verify_components.py <pbi-doc-gen checkout> <adf-doc-gen checkout>

## `sql-agent`

`sql-agent` (`sqlagent-doc-gen`, package `sqlagentdocgen`) has no standalone predecessor: it was written here on the
`feature/sql-agent-engine` branch, starting at `bff031f`, to the design in *SQL Agent Job Analysis — Design Document v4* (v4.1 since the conformance review; kept with the
owner's project documents, not in this repository).
`scripts/verify_components.py` does not apply to it.

It **depends on `adf-doc-gen`** and imports one module from it: `adfdocgen.sql_harvest.harvest_sql`, the token-based T-SQL
harvester, which reports the tables a T-SQL step reads and writes and the procedures it executes. The harvester is reused, not
copied. This coupling was accepted in design review 2 (Option A): neither engine has a third-party dependency, and the
harvester moves to a shared package only if a third consumer appears. A change to `sql_harvest` is therefore a change to both
engines; run both suites. Because the harvester reports read, write and execute only, the SQL Agent engine never reports a
delete from T-SQL.

Committed test inputs are synthetic or adversarial and are labelled so. No third-party job script is vendored: the public
scripts the parser is checked against are pinned by commit and checksum in `samples/sql-agent-corpus.json`
(`scripts/sql_agent_corpus.py`).
