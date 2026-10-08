# UI rework: gate evidence

Specification: "BI Doc Platform: UI rework spec" (7 October 2026). Branch `claude/ui-rework`, one commit per step,
then fixes from an independent review. Engine `pbi-doc-gen` 0.5.0.

| Step | Commit | Done when (spec) | Evidence |
|---|---|---|---|
| 0 | — | Cause recorded | Closed in the spec (8 October 2026) |
| 1 | `0c640d7` | Tables search, finder, object navigation, Back/Forward, hub | `tests/browser_navigation.cjs`; "Mth of year" check in `scripts/check_sample_html.cjs`; page and visual addresses in `tests/browser_report_view.cjs` |
| 2 | `050e67f`, `55512eb` | Cleaner covers `user:…@host`, bearer headers; whole artifact, both engines | `packages/engines/tests/test_projection.py`, `test_generate.py`; real reports and ADF templates; a `query` is withheld by its `queryKind` since `55512eb` |
| 3 | `ba29e70` | Power Query view, statuses, groups, "used by" | `tests/test_power_query.py`, `browser_power_query.cjs`; 179 queries / 2 folders in 29 real reports |
| 4 | `685901b` | Applied Steps from a real parse; reader gaps | `tests/test_m_steps.py`, `test_power_query_corpus.py`; 922 steps, 98% described |
| 5 | `9e872b2` | Table kinds, fx everywhere, calculation tabs | `tests/test_table_kinds.py`, `check_model_kinds.cjs`, `browser_model_kinds.cjs` |
| 6 | `b2e2b6a`, `0a334b3` | Seven sections, migration map, Overview counts = lists, versions on every path | `tests/test_sections.py`, `check_sections.cjs`, `browser_sections.cjs`, `overview_counts.cjs` on every sample; engine, legacy, bidoc generate/batch/worker version tests |
| 7 | `378120b` | Relationship surface, side panel, list tab, plain SVG | `tests/test_relationship_surface.py`, `check_relationships.cjs`, `browser_relationships.cjs`; `view_geometry.cjs` on every sample and the 29 real reports |
| 8 | `310a1f5` | One view per page, Filters pane, page types on every format, "page type not recorded", Bookmarks | `tests/test_page_types.py` (PBIR, PBIR in a PBIX, PBIP report.json, PBIX Layout, pbi-tools extract), `check_report_view.cjs`, `browser_report_view.cjs`; real reports: 218 ordinary, 31 tooltip, 14 drillthrough pages |

Checks on every step: all seven suites pass (901 tests at `55512eb`); every sample document passes
`check_sample_html.cjs`, including every view at 390 px; the large-document sample is written locally
(110 MiB, status `local_only`) and the over-ceiling sample is refused (exit 4, nothing written); `components/adf`
is unchanged and the 95 ADF templates give the same output sizes.

Independent review (a separate agent, after Step 8) found a shared-document SQL leak that predated the branch,
missing page history, an inflated Filters count, a phone-width regression and a print cut-off; all were fixed in
`55512eb` and re-verified by the same reviewer.

PR review at `1170f04` found three more, each fixed with a regression test that fails on the old code:
- Step 3: a record field, parameter or nested step named like a query hid that query everywhere in the expression,
  dropping it from upstream and "used by". Names now resolve within their scopes (`m_steps.references`;
  `tests/test_m_steps.py`, `test_power_query.py`). A second review found a scope still ran past the enclosing
  `in`, `else` or `otherwise` when the function or nested let was the last binding; it now ends there, and at
  a try's `catch` (third review).
- Step 1: a library target for a query carried only its name, so of two queries named alike the first opened and
  was reported exact. Targets now carry the query id; a name lookup after a missing id is not exact
  (`packages/engines/tests/test_generate.py`, `tests/check_query_cases.cjs`).
- Change 2: a let that returns an earlier step no longer says the later steps are unused; M evaluates whatever
  the result refers to, in any written order (`tests/check_query_cases.cjs`).

Still open:
- Final gate: regenerate the reporting user's own file and walk through it with them.
- pbi-tools bookmark groups are not read (no sample has one).
- Shared documents published before `55512eb` from a report with native SQL whose select list is plain columns
  with aliases may contain that SQL; regenerate and republish them.
