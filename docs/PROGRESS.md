# Implementation progress

Living record of work against `docs/Power-BI-Platform-Agent-Handoff.md`. Newest entries at the bottom of each section.

## Backlog status

| ID | Status | Notes |
|---|---|---|
| B01 | Done except Windows packaging spike (blocked: no Windows host) | Findings: `docs/b01/BASELINE.md`; scripts: `spikes/b01/` |
| B02 | In progress | Envelope v1 schema, validator, hash and golden fixtures done; identity/binding mappings drafted. Remaining: ADF scope descriptors, legacy-import native-schema mapping, freeze review |
| B03–B16 | Not started | |
| W1 | Probe passed | Windows CI builds and runs a PyInstaller + pywebview exe. Full packaging of the generator is B09 |
| W2 | Waiting on owner | Real PBIX extraction on the owner's Windows machine with Power BI Desktop + pbi-tools |

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

### 2026-09-28 — Owner decisions applied; engine packaging; W1 probe
- ADR 0001 recorded. pbi-tools licence verified from upstream `LICENSE` at `main` and tag `1.2.0`: AGPL-3.0.
- Engine packaging PRs (pyproject, console script, templates as package data, wheel install test):
  pbi-doc-gen [#2](https://github.com/Ntmashaba/pbi-doc-gen/pull/2) (166 tests OK, `run_ci.py` OK with no skips);
  adf-doc-gen [#2](https://github.com/Ntmashaba/adf-doc-gen/pull/2) (29 tests OK). Engine CLI behaviour unchanged.
- W1 probe ([run](https://github.com/Ntmashaba/bi-doc-platform/actions/runs/36424226145)): `windows-latest` = Windows Server 2025 (10.0.26100),
  Python 3.11.9, WebView2 153.0; PyInstaller 6.22.3 + pywebview 6.2.1 build OK; frozen exe ran and read its bundled asset.
  **Not covered:** GUI window rendering (headless runner), installer tooling, engine generation inside the exe (B09).

### 2026-09-28 — B02 started
- `packages/contracts` (`bi-doc-contracts`, standard library only): envelope v1 JSON Schema, validator that interprets the schema file,
  manifest locate/embed/hash, strict JSON, size limits, cross-field and anchor checks.
- Golden fixtures built by `tests/build_fixtures.py` via the real `embed_manifest` (valid PBI, valid ADF with CRLF, escaped script-like
  text; invalid unsupported version, duplicate manifest, hash mismatch, missing anchor). 25 tests OK, including agreement with the
  reference `jsonschema` library.
- ADR 0001 policy reflected in the envelope: `projection.profile` (local/shared) and `projection.options.query_code` (included/withheld).
- Identity/binding draft `docs/contracts/identity-and-bindings-v1.md`. Found: TMDL reader and BIM path both drop `lineageTag` (B03 engine change).
- Added Linux CI for the contracts package (fails on skipped tests).

### Next
- B02: ADF scope descriptors (factory vs. selection), native-schema mapping for legacy import (`pbi-doc-gen/2`, `adf-doc-gen/2`), then freeze.
- B03: engine changes (surface `lineageTag`; keep SQL port/case in ADF `physical_key`; Delete as its own operation; escape `<` in `DATA`);
  shared projection with the *Include query code* option; adapters emitting envelope v1.
