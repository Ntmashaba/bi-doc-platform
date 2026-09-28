# ADR 0001 — Product-owner decisions after B01

Date: 28 September 2026 · Status: accepted

## Guiding rule

Local generation and shared publication have **separate, explicit policies**. A security control for shared publication must never silently remove a documentation feature from local output. When shared output omits something, the omission is recorded in the projection report and shown to the reader.

## 1. Query code (M/SQL)

- **Local documentation:** raw M/SQL stays available, exactly as the engines produce it today.
- **Shared publication:** a publisher option, *Include query code*, defaults to **off** for R1.
  - When off, query code is removed from the published native payload, the rendered view **and** the search index, not merely hidden on screen. The projection report lists the omitted field paths (never their values).
  - When on, the publisher has explicitly chosen to share code. The UI states that the code is shared as written. Automated cleaning (for example redacting `Pwd=` or URL tokens) may run, but it is never presented as a guarantee that the code contains no secrets.
- The envelope records the choice in `projection.policy` (`query_code: "included" | "withheld"`) so importers and viewers can display it.

## 2. pbi-tools

- R1 keeps pbi-tools as a **separately installed prerequisite**. `doctor` detects it and gives setup guidance. It is not bundled.
- Licence verified from upstream on 28 September 2026: `github.com/pbi-tools/pbi-tools` `LICENSE` at both `main` and tag `1.2.0` (the version of the binary committed in pbi-doc-gen) is **GNU AGPL-3.0**. Any future redistribution decision must start from that, with legal review.
- The committed `pbi-tools.exe` in pbi-doc-gen is a development convenience. It is not a distribution channel for the product.

## 3. Windows validation is two separate checks

| Check | Where | Proves |
|---|---|---|
| W1 — Packaging build | Windows CI runner (GitHub Actions `windows-latest`) | PyInstaller/pywebview build, installer build, CLI smoke on the built executable with PBIP/ADF inputs |
| W2 — Real PBIX extraction | The product owner's Windows machine with Power BI Desktop + pbi-tools installed | pbi-tools extraction of the real sample PBIX files, representative sizes, generation end-to-end |

These are reported separately. A green W1 says nothing about W2. Runner availability must be confirmed by an actual workflow run before W1 is relied on.

## 4. Engine packaging

- Add `pyproject.toml` to both engine repositories. Keep `generate_docs.py` and its CLI behaviour unchanged, and ship the HTML/CSS/JS templates as package data.
- The platform consumes **versioned engine packages** (pinned versions/revisions), not sibling checkouts.
