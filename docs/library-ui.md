# Library shell and viewers (B06b)

Source: `apps/library/frontend/src/*.ts` (TypeScript 5.9, strict). Compiled with `npm run build` into `apps/library/bidoc_library/static/`, which is committed. The production server serves those files and needs no Node. CI recompiles and fails if the committed output differs.

## Screens

| Route | What it does |
|---|---|
| `#/`, `#/power_bi`, `#/adf` | All / Power BI / Data Factory. Search box (browser search over `/search-index`, same semantics as the server), filters (business area, environment, owner, tag, archived for publishers), document list with type badges, update date, environment, owner and tags. Separate empty states for "no documentation yet", "no documents match these filters" and "no matching results". **Import documentation** (publishers) and **Download generator** (honestly says no installer is available until B11). |
| `#/import` | Choose an HTML file. The preview reads its manifest without running it and shows title, type, generation date and whether it will be a **new document**, a **new version of** an existing one (its ETag is sent as `If-Match`) or a **duplicate**. **Include query code** is off by default and says cleaning is not a guarantee. The idempotency key is kept per file, so a retry never publishes twice. |
| `#/documents/{id}` | Details, classification, generation and publication dates, version history with Open and Download, Archive/Restore with confirmation. |
| `#/view/{id}/{revision}?object=&generation=&section=` | Breadcrumbs, object selector, Download, the sandboxed viewer and the **Related documentation** panel. |
| `#/processing` | **Process PBIX** (publishers; R3, B15). Upload a `.pbix` or a `.zip` of a PBIP project as a new document or a new version of an existing Power BI document. The upload shows its progress. While no worker is ready, the page says so, links to the generator, and the upload button is disabled; the server refuses too (`409 WORKER_UNAVAILABLE`). The job list refreshes every 2 s while work is active, showing state and stage, the attempt when it is retried, the error on failure, **Open document** on success, and **Cancel** or **Retry** as applicable. |
| `#/settings` | Library version, access mode, storage, health, search mode, processing-worker status and last heartbeat. Administrators also see the workers (readiness, inputs, versions, last heartbeat, **Revoke**) and can **Enroll** one; its token is shown once. No secrets are shown otherwise. |

## Related documentation panel

- **Groups:** upstream pipelines that write this, downstream reports that read this, other readers, deletions, and manual links.
- **Each link** shows type, document and object, confidence ("Exact (static evidence)", "Possible", "Manual — user asserted"), environment, status (Needs review / Archived) and expandable evidence (both endpoints, resolutions, notes).
- **Evidence state** is always shown: computed at, updating (prior evidence labelled), pinned for an earlier version, or unavailable.
- With no links, the panel says "No relationship found in the indexed documents."
- **Following a link** opens the exact counterpart revision and object, with the generation pinned in the URL. Back restores the origin.
- **Publishers** can add a manual link (target document and object, kind, reason). A stale selection asks for a reload.

## Viewer isolation and navigation (bi-doc-viewer protocol v1)

- **The viewer frame** is `<iframe sandbox="allow-scripts">`, without `allow-same-origin`. The response is also sandboxed by CSP (`connect-src 'none'`, `form-action 'none'`), so the document cannot read the shell, its cookies or the API, submit forms, or navigate the top window.
- **Handshake:** on load the shell sends `hello` with a random per-load channel and the revision ID. The engine page (pbi-doc-gen / adf-doc-gen 0.3.0) answers `viewer-ready` with its registered views.
- **Navigation:** the shell then sends `navigate-object` with a view ID and string arguments from the document's validated navigation registry. The page opens the tab, expands the object and replies `navigated` with whether it found the exact object.
- **Validation:** both sides check the sender window, protocol, version, channel and revision, and the exact shape. Messages carry no HTML, scripts or URLs.
- **Fallbacks:**
  - a viewer that never becomes ready gets a notice that it opened at the start;
  - an object not found in that version gets a "nearest section" notice.
- **The shell page** has `script-src 'self'`, no inline script, `frame-ancestors 'none'`, and renders every document value as text. In local mode it carries the installation's session secret for its own requests; other sites cannot read it (no CORS, and the Host header is checked).

## Checks

- `apps/library/tests/test_frontend.py`: the browser search equals the Python reference over generated cases (accented and non-Latin text included); shell headers and static whitelist.
- `apps/library/tests/check_browser.py` runs `browser_e2e.cjs` in Chromium against a real server:
  - browse, filter and search;
  - Power BI source → exact ADF activity opened inside the viewer → reverse link → Back;
  - measure selection;
  - isolation probes: parent, cookies, fetch and top navigation blocked; forged messages ignored;
  - import with preview;
  - no unexpected console errors or failed requests.
