# Large documents and search (A41)

`apps/library/tests/check_large_documents.py` builds representative large inputs,
publishes them into one library, and measures the costs. CI runs it in the
`large-documents` job and fails on broken behaviour or on a result far outside the pilot
budget (publish 60 s, rebuild 120 s, peak 1.5 GiB, search 500 ms per query).

## Inputs (scale 1)

- **Power BI model:** 150 tables, 3,000 columns and 1,500 measures, each with a
  description and DAX. Every table reads a warehouse table.
- **Data Factory:** 400 pipelines, each with 5 activities (Copy, Lookup, Wait,
  SetVariable, Delete), 800 datasets, and writes to the same warehouse tables, so the
  documents link.

## Results (development container, Python 3.11, with tracemalloc on)

| Measure | Power BI | Data Factory |
|---|---|---|
| Generate | 11.5 s | 9.3 s |
| Artifact size | 23.9 MB | 8.6 MB |
| Publish (validate, project, store, commit) | 16.0 s | 12.6 s |

| Library-wide | Value |
|---|---|
| Peak Python memory: publish / rebuild / search | 266 / 132 / 119 MiB |
| Full derived rebuild (search index plus relationship generation) | 2.0 s |
| Relationship links found on the model | 800 |
| Search index | 884 KB (browser search) |
| Search, slowest of 7 queries | 55 ms |
| Server search equals browser search | yes, for every query |

tracemalloc roughly doubles Python run time, so these times are upper bounds. The earlier
real-document measurement (124 documents from the Microsoft samples and the ADF template
gallery) is in `docs/relationships.md`.

## What this means

- **The upload limit is the first thing a large model meets.** At 23.9 MB, the model is
  close to the 25 MiB default `MAX_HTML_BYTES`. A client with larger models should raise
  `MAX_HTML_BYTES` (and `MAX_MANIFEST_BYTES` if needed). Import then takes longer, and the
  0.5 GiB pilot container should be raised to 1 GiB.
- **Search stays small.** The index holds titles, tags and section text, not every
  object, so even large documents add under 1 MB.
- **Server search mode:** when the index grows past `CLIENT_SEARCH_INDEX_BYTES` (8 MiB by
  default):
  - `/capabilities` reports `search_mode: server`;
  - the browser loads the catalogue without section text
    (`/search-index?sections=false`);
  - text queries go to `/api/v1/search`, which runs the same reference implementation.

  Its results equal the browser search; the tests and the check above compare them, and a
  browser step drives the UI in server mode. The UI shows up to 500 hits with the total
  count.
- **Rebuilds** read every current document's manifest. At pilot scale they take seconds;
  the one-replica pilot runs them in-process after each commit.
