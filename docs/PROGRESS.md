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

### Owner decisions (resolved — see `docs/decisions/0001-b01-owner-decisions.md`)
1. Query code: local keeps raw M/SQL; shared publication has an explicit *Include query code* option, off by default, removing code from payload and search index when off.
2. pbi-tools: separate prerequisite for R1. Licence verified upstream (main and tag 1.2.0): AGPL-3.0.
3. Windows: W1 packaging build on Windows CI and W2 real PBIX extraction on the owner's machine, reported separately.
4. Engines get `pyproject.toml`; platform consumes versioned engine packages.

### Next
- B02: envelope JSON Schema, hash implementation and golden fixtures; engine ID mappings (check PBI `lineageTag`); binding/endpoint schema.
