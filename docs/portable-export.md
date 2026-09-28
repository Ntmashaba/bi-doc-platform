# Portable offline export (B12; A12, A38)

```
bidoc export-library OUTPUT INPUT... [--only DOCUMENT_ID]... [--title TEXT] [--json]
```

`INPUT` is a generated HTML file or a folder of them. The command writes a folder that
opens by double-clicking `index.html`, from `file://`, with no network:

| Path | Contents |
|---|---|
| `index.html` | catalogue, search, related-object links and the viewer. Data and code are inline, because `file://` cannot fetch or load modules. |
| `documents/<id>.html` | each selected document, re-rendered read-only by the trusted engines from its manifest |
| `export.json` | what was exported: format `bidoc-portable/1`, date, versions, documents, rule version, warnings |

## Rules

- Only files with a publication manifest are exported. Local-only output is skipped with a
  warning. When the same document appears more than once, the newest one is used.
- Documents are rebuilt from the native payload under the shared projection, the same way
  the library does. The input HTML is never copied as is.
- Relationships are detected once over all inputs. With `--only`, only the selected
  documents are copied, and links to other documents show "Not included in this export".
- The export is a static snapshot. It never updates itself; run the command again to
  rebuild it. The output folder must be empty or a previous export, which is replaced and
  never merged.
- `index.html` has a Content-Security-Policy with no network (`connect-src 'none'`), and
  documents open in `sandbox="allow-scripts"` iframes. Object navigation uses the same
  `bi-doc-viewer` protocol as the hosted library, and the browser's Back button returns to
  the previous object.

## Checks

- `apps/generator/tests/test_export.py` covers contents, partial exports, refusals and
  rebuilds.
- `apps/generator/tests/check_offline_browser.py` builds a complete and a partial export
  and drives them over `file://`:
  - search;
  - ADF activity → related Power BI object → Back;
  - sandbox isolation;
  - "Not included";
  - zero network requests and no console errors.

  CI runs it in Chromium (the `frontend` job) and in Microsoft Edge on Windows
  (`generator-windows`, `--channel msedge`).
