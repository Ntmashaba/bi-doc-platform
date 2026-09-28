# Implementation progress

Living record of work against `docs/Power-BI-Platform-Agent-Handoff.md`. Newest entries at the bottom of each section.

## Backlog status

| ID | Status | Notes |
|---|---|---|
| B01 | Done except Windows packaging spike (blocked: no Windows host) | Findings: `docs/b01/BASELINE.md`; scripts: `spikes/b01/` |
| B02–B16 | Not started | |

## Log

### 2026-09-28 — B01 started
- Branch `claude/nifty-thompson-56taho`.
- Engines pinned for review: pbi-doc-gen `a7d5565cc1d237f8efc4dc96e47b756d15c1b14a`, adf-doc-gen `7c8cfe501b9d1e79eb53478779056ef9f1c9d329`.
- Ran both suites: pbi-doc-gen 163 OK, adf-doc-gen 26 OK (Python 3.11.15, Linux).
- Generated synthetic samples; measured HTML/payload/search-term sizes (real PBIX sizes unmeasured — needs Windows).
- Safe-projection probes: all 5 seeded PBI markers leak; PBI embeds absolute `model.sourcePath`; ADF redacts credentials but leaks SQL query literals.
- Endpoint probe: SQL port collision and SQL case-folding happen in the ADF engine (`common.py`), not only the bridge; Delete is emitted as a sink.
- Identity survey: no explicit object IDs in either engine; PBI `lineageTag` is the lead to investigate in B02.
- Legacy import spike: native payload recoverable from existing HTML without script execution.
- Windows packaging spike: **not run** (no Windows host) — explicit open gate.

### Open decisions for the product owner
1. Keep default withholding of Power BI M/SQL text for R1, or fund an M sanitizer? (See BASELINE §3, §9.)
2. Confirm pbi-tools stays an external prerequisite (not bundled); confirm its licence.
3. Provide a Windows machine or Windows CI runner for the packaging spike.
4. Add upstream `pyproject.toml` packaging to both engines (recommended), or vendor them as pinned git dependencies?

### Next
- B02: envelope JSON Schema, hash implementation and golden fixtures; engine ID mappings (check PBI `lineageTag`); binding/endpoint schema.
