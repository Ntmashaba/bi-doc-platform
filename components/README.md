# Engine components

`power-bi` and `adf` are the documentation engines, previously the standalone repositories
`Ntmashaba/pbi-doc-gen` and `Ntmashaba/adf-doc-gen`.

| Component | Imported from | Commit |
|---|---|---|
| `power-bi` | `Ntmashaba/pbi-doc-gen` | `0b04cf41d1cff0e4d7fc3587fff6e14f86fd405f` |
| `adf` | `Ntmashaba/adf-doc-gen` | `960b4bc165efff415d1adb4932fb39196fc9eff1` |

The trees are copies of those commits with two deliberate differences:

- The CLI moved from the root `generate_docs.py` into the package (`pbidocgen/cli.py`, `adfdocgen/cli.py`) so the
  wheel contains it; `generate_docs.py` remains as a thin compatibility shim, and the console-script entry point and
  packaging test follow the move. The moved code is otherwise unchanged.
- `power-bi/pbi-tools/` (Windows binaries) is not imported, and `power-bi/pbix-samples/` holds only the three small
  DP-500 `.pbix` files (about 170 KB) that `test_pbixray_extract.py` reads; the other sample `.pbix` files (about 74 MB)
  are not imported. Tests that read those are unchanged from the standalone repository.

To re-check the copies against the original repositories (each checked out at the commit above):

    python scripts/verify_components.py <pbi-doc-gen checkout> <adf-doc-gen checkout>
