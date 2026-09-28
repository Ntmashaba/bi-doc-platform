/* Library shell (handoff sections 4 and 16). All document data is written with
   textContent/DOM nodes, never innerHTML. State lives in the URL hash, so Back restores
   the origin view, filters and selected object. */
import { search } from "./search.js";
import { ViewerChannel } from "./protocol.js";
// ---- API --------------------------------------------------------------------------
const SESSION = document.querySelector('meta[name="bidoc-session"]')?.content || "";
const API = "/api/v1";
class ApiError extends Error {
    constructor(status, code, message, details = {}) {
        super(message);
        this.status = status;
        this.code = code;
        this.details = details;
    }
}
async function api(path, init = {}) {
    const method = init.method || "GET";
    const headers = { Accept: "application/json", ...(init.headers || {}) };
    if (method !== "GET") {
        headers["X-Requested-With"] = "bidoc";
        if (SESSION)
            headers["X-Bidoc-Session"] = SESSION;
    }
    let body = init.body;
    if (init.json !== undefined) {
        body = JSON.stringify(init.json);
        headers["Content-Type"] = "application/json";
    }
    const res = await fetch(API + path, { method, headers, body, credentials: "same-origin" });
    const text = res.status === 204 || res.status === 304 ? "" : await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok && res.status !== 304) {
        const e = (data && data.error) || {};
        throw new ApiError(res.status, e.code || "HTTP_" + res.status, e.message || res.statusText, e.details || {});
    }
    return { status: res.status, data: data, headers: res.headers };
}
function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === false)
            continue;
        if (typeof v === "function")
            el.addEventListener(k.replace(/^on/, ""), v);
        else if (v === true)
            el.setAttribute(k, "");
        else
            el.setAttribute(k, String(v));
    }
    for (const c of children)
        if (c !== null && c !== undefined && c !== false)
            el.append(c instanceof Node ? c : String(c));
    return el;
}
function kids(...c) {
    return c.filter((x) => x !== null && x !== undefined && x !== false).map((x) => (x instanceof Node ? x : String(x)));
}
// Each render builds into its own container and is swapped in only if it is still the
// latest navigation, so overlapping renders can never show a stale view.
let target = document.createElement("div");
let renderSeq = 0;
const main = () => target;
function mount(...nodes) { main().replaceChildren(...kids(...nodes)); }
function notice(kind, text, ...extra) {
    return h("p", { class: `notice ${kind}`, role: kind === "error" ? "alert" : "status" }, text, ...extra);
}
const TYPE_LABEL = { power_bi: "Power BI", adf: "Data Factory" };
function typeBadge(t) { return h("span", { class: `badge type-${t}` }, TYPE_LABEL[t] || t); }
function date(s) {
    if (!s)
        return "—";
    const d = new Date(s);
    return isNaN(d.getTime()) ? s : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
function errorText(e) {
    return e instanceof ApiError ? `${e.message} (${e.code})` : e instanceof Error ? e.message : String(e);
}
function route() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, query] = raw.split("?", 2);
    return { path: path ? path.split("/").map(decodeURIComponent) : [], params: new URLSearchParams(query || "") };
}
function href(path, params = {}) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params))
        if (v)
            q.set(k, v);
    const qs = q.toString();
    return "#/" + path.map(encodeURIComponent).join("/") + (qs ? "?" + qs : "");
}
let caps = null;
let viewer = null;
async function render() {
    viewer?.close();
    viewer = null;
    const seq = ++renderSeq;
    const container = document.createElement("div");
    target = container;
    const r = route();
    try {
        caps = caps || (await api("/capabilities")).data;
        if (r.path[0] === "import")
            await importView();
        else if (r.path[0] === "downloads")
            await downloadsView();
        else if (r.path[0] === "tokens")
            await tokensView();
        else if (r.path[0] === "documents" && r.path[1])
            await detailsView(r.path[1]);
        else if (r.path[0] === "view" && r.path[1] && r.path[2])
            await viewerView(r.path[1], r.path[2], r.params);
        else
            await homeView(r.path[0] === "power_bi" || r.path[0] === "adf" ? r.path[0] : null, r.params);
    }
    catch (e) {
        container.replaceChildren(...kids(h("h1", {}, "Something went wrong"), notice("error", errorText(e)), h("p", {}, h("a", { href: "#/" }, "Back to the library"))));
    }
    if (seq !== renderSeq)
        return; // a newer navigation owns the page
    document.body.classList.toggle("viewing", r.path[0] === "view");
    document.getElementById("main").replaceChildren(container);
}
let index = null;
let indexEtag = "";
// In server search mode (a library too large to search in the browser) the index holds only
// catalogue fields, and text queries go to /search, which has the same semantics.
const serverSearch = () => caps?.search_mode === "server";
async function loadIndex() {
    const res = await api(serverSearch() ? "/search-index?sections=false" : "/search-index", { headers: indexEtag ? { "If-None-Match": indexEtag } : {} });
    if (res.status !== 304) {
        index = res.data;
        indexEtag = res.headers.get("ETag") || "";
    }
    return index;
}
async function homeView(type, params) {
    const q = params.get("q") || "";
    const filters = { business_area: params.get("business_area"), environment: params.get("environment"),
        owner: params.get("owner"), tag: params.get("tag") };
    const archived = params.get("archived") === "1" && !!caps?.can_publish;
    const idx = await loadIndex();
    const base = type ? [type] : [];
    const setParam = (key, value, replace = false) => {
        const next = { q, ...filters, archived: archived ? "1" : null, [key]: value || null };
        const target = href(base, next);
        if (replace) {
            history.replaceState(null, "", target);
            render();
        }
        else
            location.hash = target;
    };
    const values = (pick) => [...new Set(idx.documents.filter((d) => !type || d.document_type === type).flatMap(pick).filter(Boolean))].sort();
    const filterSelect = (key, label, options) => h("label", {}, label, h("select", { onchange: (e) => setParam(key, e.target.value) }, h("option", { value: "" }, "Any"), ...options.map((o) => h("option", { value: o, selected: filters[key] === o }, o))));
    let timer = 0;
    const searchBox = h("input", { type: "search", id: "q", value: q, placeholder: "Search titles, measures, pipelines, sources…",
        "aria-label": "Search documentation", oninput: (e) => {
            window.clearTimeout(timer);
            const v = e.target.value;
            timer = window.setTimeout(() => setParam("q", v, true), 250);
        } });
    const header = h("section", { class: "toolbar" }, h("nav", { class: "types", "aria-label": "Document types" }, ...[["", "All documentation"], ["power_bi", "Power BI"], ["adf", "Data Factory"]].map(([t, label]) => h("a", { href: href(t ? [t] : [], { q }), "aria-current": (type || "") === t ? "page" : undefined }, label))), searchBox, h("div", { class: "filters" }, filterSelect("business_area", "Business area", values((d) => [d.classification.business_area])), filterSelect("environment", "Environment", values((d) => [d.classification.environment])), filterSelect("owner", "Owner", values((d) => [d.classification.owner])), filterSelect("tag", "Tag", values((d) => d.tags)), caps?.can_publish ? h("label", { class: "check" }, h("input", { type: "checkbox", checked: archived,
        onchange: (e) => setParam("archived", e.target.checked ? "1" : null) }), "Archived") : null), h("div", { class: "actions" }, caps?.can_publish ? h("a", { class: "button", href: "#/import" }, "Import documentation") : null, caps?.can_publish ? h("a", { class: "button secondary", href: "#/tokens" }, "Publishing tokens") : null, h("a", { class: "button secondary", href: "#/downloads" }, "Download generator")));
    const results = h("section", { id: "results", "aria-live": "polite" });
    mount(h("h1", {}, type ? `${TYPE_LABEL[type]} documentation` : "All documentation"), header, idx.state !== "ready" ? notice("warn", "Search results may be out of date: the index is being updated.") : null, results);
    if (q && !archived) {
        let hits, total;
        if (serverSearch()) {
            const sq = new URLSearchParams({ q, limit: "500" });
            if (type)
                sq.set("document_type", type);
            for (const [k, v] of Object.entries(filters))
                if (v)
                    sq.set(k, v);
            const res = await api("/search?" + sq.toString());
            hits = res.data.items;
            total = res.data.total;
        }
        else {
            hits = search(idx.documents, q, { document_type: type, ...filters });
            total = hits.length;
        }
        results.replaceChildren(h("p", { class: "count" }, `${total} ${total === 1 ? "result" : "results"} for “${q}”` +
            (total > hits.length ? ` (showing the first ${hits.length})` : "")), hits.length ? h("ol", { class: "hits" }, ...hits.map(hitRow))
            : notice("info", idx.documents.length ? "No matching results. Try fewer or different words, or clear the filters."
                : "There is no documentation in the library yet."));
        return;
    }
    const query = new URLSearchParams({ limit: "50", archived: archived ? "true" : "false" });
    if (type)
        query.set("document_type", type);
    for (const [k, v] of Object.entries(filters))
        if (v)
            query.set(k, v);
    const page = await api("/documents?" + query.toString());
    const list = h("ol", { class: "documents" }, ...page.data.items.map(documentRow));
    results.replaceChildren(page.data.items.length ? list : emptyLibrary(type, archived, !!(filters.business_area ||
        filters.environment || filters.owner || filters.tag)));
    let cursor = page.data.next_cursor;
    if (cursor) {
        const more = h("button", { type: "button", class: "secondary", onclick: async () => {
                query.set("cursor", cursor);
                const next = await api("/documents?" + query.toString());
                list.append(...next.data.items.map(documentRow));
                cursor = next.data.next_cursor;
                if (!cursor)
                    more.remove();
            } }, "Show more");
        results.append(more);
    }
}
function emptyLibrary(type, archived, filtered) {
    if (archived)
        return notice("info", "No archived documents.");
    if (filtered)
        return notice("info", "No documents match these filters.");
    const what = type ? `${TYPE_LABEL[type]} documentation` : "documentation";
    return h("div", { class: "empty" }, h("h2", {}, `No ${what} yet`), h("p", {}, "Generate a document with the generator (bidoc generate … --profile shared), then import the HTML file here."), caps?.can_publish ? h("a", { class: "button", href: "#/import" }, "Import documentation") : null);
}
function documentRow(d) {
    return h("li", {}, h("div", { class: "row-head" }, typeBadge(d.document_type), h("a", { href: href(["view", d.document_id, d.current_revision_id]) }, d.title), d.archived ? h("span", { class: "badge archived" }, "Archived") : null), d.description ? h("p", { class: "muted" }, d.description) : null, h("p", { class: "meta" }, `Updated ${date(d.updated_at)}`, d.classification.environment ? ` · ${d.classification.environment}` : "", d.classification.owner ? ` · Owner: ${d.classification.owner}` : "", " · ", h("a", { href: href(["documents", d.document_id]) }, "Details and versions")), d.tags.length ? h("p", { class: "tags" }, ...d.tags.map((t) => h("span", { class: "tag" }, t))) : null);
}
function hitRow(hit) {
    return h("li", {}, h("div", { class: "row-head" }, typeBadge(hit.document_type), h("a", { href: href(["view", hit.document_id, hit.revision_id], { section: hit.section_id }) }, hit.title + (hit.section_title ? ` › ${hit.section_title}` : ""))), hit.snippet ? h("p", { class: "snippet" }, hit.snippet) : null);
}
function releaseCard(r) {
    return h("div", { class: "card release" }, h("h2", {}, `Generator ${r.version}`, r.approved ? "" : " (awaiting approval)"), !r.signed ? notice("warn", `${r.label || "Development build"}: this installer is not code-signed, so Windows may warn before it runs.`) : null, h("dl", { class: "kv" }, h("dt", {}, "Released"), h("dd", {}, date(r.approved_at || r.created_at)), h("dt", {}, "Installer"), h("dd", {}, `${r.filename} (${(r.size_bytes / 1048576).toFixed(1)} MB, Windows x64)`), h("dt", {}, "SHA-256"), h("dd", {}, h("code", {}, r.sha256)), h("dt", {}, "Also needed"), h("dd", {}, r.prerequisites.length ? r.prerequisites.join("; ") : "nothing else")), r.release_notes ? h("p", { class: "notes" }, r.release_notes) : null, r.download_url ? h("p", {}, h("a", { class: "button", href: r.download_url }, "Download installer")) : null, !r.approved && caps?.can_administer ? h("p", {}, h("button", { type: "button", onclick: async () => {
            await api(`/releases/${encodeURIComponent(r.version)}/approve`, { method: "POST" });
            await downloadsView();
        } }, "Approve for download")) : null);
}
async function sha256Hex(file) {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function downloadsView() {
    const list = (await api("/releases")).data.items;
    const upload = caps?.can_administer ? (() => {
        const out = h("div", { "aria-live": "polite" });
        const file = h("input", { type: "file", id: "release-file", accept: ".exe", required: true });
        const version = h("input", { type: "text", id: "release-version", placeholder: "0.3.0", required: true });
        const notes = h("textarea", { id: "release-notes" });
        const prereq = h("input", { type: "text", id: "release-prereq",
            value: "Power BI Desktop and pbi-tools (PBIX only); Microsoft Edge WebView2 Runtime" });
        return h("form", { class: "card", onsubmit: async (e) => {
                e.preventDefault();
                const f = file.files?.[0];
                if (!f)
                    return;
                out.replaceChildren(notice("info", "Checking and uploading…"));
                const body = new FormData();
                body.append("file", f, f.name);
                body.append("version", version.value.trim());
                body.append("sha256", await sha256Hex(f));
                body.append("release_notes", notes.value);
                body.append("prerequisites", JSON.stringify(prereq.value.split(";").map((x) => x.trim()).filter(Boolean)));
                try {
                    await api("/releases", { method: "POST", body });
                    await downloadsView();
                }
                catch (err) {
                    out.replaceChildren(notice("error", errorText(err)));
                }
            } }, h("h2", {}, "Add a release (administrators)"), h("p", { class: "muted small" }, "Upload the installer from the CI build. It is only offered for download after approval."), h("p", {}, h("label", { for: "release-file" }, "Installer (.exe)"), file), h("p", {}, h("label", { for: "release-version" }, "Version"), version), h("p", {}, h("label", { for: "release-prereq" }, "Also needed (separate with ;)"), prereq), h("p", {}, h("label", { for: "release-notes" }, "Release notes"), notes), h("button", { type: "submit" }, "Upload"), out);
    })() : null;
    mount(h("p", {}, h("a", { href: "#/" }, "← Library")), h("h1", {}, "Download the generator"), h("p", { class: "muted" }, "The generator documents Power BI and Data Factory on your computer and can publish to this library."), list.length ? h("div", {}, ...list.map(releaseCard))
        : notice("info", "No generator installer is available from this library yet. Ask your administrator for the generator."), upload);
}
async function tokensView() {
    if (!caps?.can_publish) {
        mount(h("h1", {}, "Publishing tokens"), notice("error", "Publisher role required."));
        return;
    }
    const items = (await api("/publish-tokens")).data.items;
    const out = h("div", { id: "token-new", "aria-live": "polite" });
    const label = h("input", { type: "text", id: "token-label", maxlength: "100", required: true,
        placeholder: "e.g. Finance laptop" });
    const days = h("select", { id: "token-days" }, ...[1, 7, 14, 30].map((d) => h("option", { value: String(d) }, `${d} day${d > 1 ? "s" : ""}`)));
    days.value = "7";
    const form = h("form", { class: "card", onsubmit: async (e) => {
            e.preventDefault();
            try {
                const r = (await api("/publish-tokens", { method: "POST", json: { label: label.value.trim(), expires_in_days: Number(days.value) } })).data;
                const field = h("input", { type: "text", readonly: true, value: r.token, id: "token-secret", class: "secret",
                    "aria-label": "New publishing token" });
                await tokensView();
                document.getElementById("token-new")?.replaceChildren(notice("warn", "Copy this token now. It is shown only once; " +
                    `it expires ${date(r.expires_at)}. In the generator: Library → Connect, or bidoc connect.`), field);
                field.select();
            }
            catch (err) {
                out.replaceChildren(notice("error", errorText(err)));
            }
        } }, h("h2", {}, "New token"), h("p", {}, h("label", { for: "token-label" }, "Name"), label), h("p", {}, h("label", { for: "token-days" }, "Valid for"), days), h("button", { type: "submit" }, "Create token"));
    mount(h("p", {}, h("a", { href: "#/" }, "← Library")), h("h1", {}, "Publishing tokens"), h("p", { class: "muted" }, "A publishing token lets the generator publish to this library as you. It can only publish; " +
        "it cannot archive, edit or manage anything else. Revoke it when a computer is lost or no longer used."), out, form, items.length ? h("table", { class: "tokens" }, h("thead", {}, h("tr", {}, ...["Name", "Created", "Expires", "Last used", "State", ""].map((c) => h("th", {}, c)))), h("tbody", {}, ...items.map((t) => h("tr", {}, h("td", {}, t.label), h("td", {}, date(t.created_at)), h("td", {}, date(t.expires_at)), h("td", {}, t.last_used_at ? date(t.last_used_at) : "never"), h("td", {}, t.state), h("td", {}, t.state === "active" ? h("button", { type: "button", class: "secondary", onclick: async () => {
            await api(`/publish-tokens/${t.token_id}`, { method: "DELETE" });
            await tokensView();
        } }, "Revoke") : "")))))
        : h("p", { class: "muted" }, "No tokens yet."));
}
const LEGACY_FIELDS = [["title", "Title"], ["environment", "Environment (e.g. Production)"],
    ["business_area", "Business area"], ["owner", "Owner"]];
async function importView() {
    if (!caps?.can_publish) {
        mount(h("h1", {}, "Import documentation"), notice("error", "Publisher role required."));
        return;
    }
    const preview = h("div", { id: "import-preview", "aria-live": "polite" });
    const outcome = h("div", { id: "import-outcome", "aria-live": "polite" });
    const includeCode = h("input", { type: "checkbox", id: "include-code" });
    const submit = h("button", { type: "submit", disabled: true }, "Publish");
    const legacyInputs = Object.fromEntries(LEGACY_FIELDS.map(([k]) => [k, h("input", { id: `legacy-${k}`, maxlength: "200" })]));
    const legacyBox = h("fieldset", { id: "legacy-fields", hidden: true }, h("legend", {}, "Catalogue details for this older document"), h("p", { class: "muted small" }, "This file has no publication manifest. It was made by a known generator, so the library " +
        "converts it: query code is withheld, the document is regenerated, and it becomes a new document."), ...LEGACY_FIELDS.map(([k, label]) => h("p", {}, h("label", { for: `legacy-${k}` }, label), legacyInputs[k])));
    let file = null, etag = null, key = "", legacy = false;
    const legacyMetadata = () => JSON.stringify(Object.fromEntries(LEGACY_FIELDS.map(([k]) => [k, legacyInputs[k].value.trim()]).filter(([, v]) => v)));
    async function showPreview() {
        if (!file)
            return;
        preview.replaceChildren(notice("info", "Checking…"));
        etag = null;
        const body = new FormData();
        body.append("file", file, file.name);
        body.append("query_code", includeCode.checked ? "included" : "withheld");
        if (legacy)
            body.append("legacy_metadata", legacyMetadata());
        let p;
        try {
            p = (await api("/imports/preview", { method: "POST", body })).data;
        }
        catch (e) {
            preview.replaceChildren(notice("error", errorText(e)));
            legacyBox.hidden = true;
            submit.disabled = true;
            return;
        }
        legacy = p.input === "legacy";
        legacyBox.hidden = !legacy;
        if (p.outcome === "new_version" && p.document_id) {
            etag = (await api(`/documents/${encodeURIComponent(p.document_id)}`)).headers.get("ETag");
        }
        const result = { new_document: "New document", new_version: "New version of an existing document",
            duplicate: "Already published (duplicate)" }[p.outcome];
        const reasons = new Map();
        for (const o of p.omissions)
            reasons.set(o.reason, (reasons.get(o.reason) || 0) + 1);
        preview.replaceChildren(h("dl", { class: "kv" }, h("dt", {}, "Title"), h("dd", {}, p.title), h("dt", {}, "Type"), h("dd", {}, TYPE_LABEL[p.document_type] || p.document_type), h("dt", {}, "Input"), h("dd", {}, legacy ? `Older document (${p.native_schema}), converted` : p.input === "zip" ? "Generated document (ZIP)" : "Generated document"), h("dt", {}, "Result"), h("dd", {}, result), h("dt", {}, "Contents"), h("dd", {}, `${p.object_count} objects, ${p.section_count} sections`), h("dt", {}, "Query code"), h("dd", {}, p.query_code === "included" ? "included" : "withheld"), h("dt", {}, "Removed for sharing"), h("dd", {}, reasons.size
            ? [...reasons].map(([r, n]) => `${n} × ${r.replace(/_/g, " ")}`).join(", ") : "nothing")), ...p.coverage_warnings.map(w => notice("warn", w)));
        submit.disabled = false;
    }
    const input = h("input", { type: "file", id: "file", accept: ".html,.zip,text/html,application/zip", required: true, onchange: async () => {
            file = input.files?.[0] || null;
            key = crypto.randomUUID(); // reused if the same file is retried
            legacy = false;
            outcome.replaceChildren();
            preview.replaceChildren();
            submit.disabled = true;
            if (!file)
                return;
            const zip = /\.zip$/i.test(file.name);
            if (file.size > ((zip ? caps?.limits.zip_bytes : caps?.limits.html_bytes) || Infinity)) {
                preview.append(notice("error", "This file is larger than the library accepts."));
                return;
            }
            await showPreview();
        } });
    for (const el of [...Object.values(legacyInputs), includeCode]) {
        el.addEventListener("change", () => { key = crypto.randomUUID(); void showPreview(); });
    }
    const form = h("form", { onsubmit: async (e) => {
            e.preventDefault();
            if (!file)
                return;
            submit.disabled = true;
            outcome.replaceChildren(notice("info", "Publishing…"));
            const body = new FormData();
            body.append("file", file, file.name);
            body.append("query_code", includeCode.checked ? "included" : "withheld");
            if (legacy)
                body.append("legacy_metadata", legacyMetadata());
            try {
                const r = await api("/imports", { method: "POST", body, headers: { "Idempotency-Key": key, ...(etag ? { "If-Match": etag } : {}) } });
                index = null;
                indexEtag = "";
                outcome.replaceChildren(notice("info", r.data.duplicate ? "This exact file was already published. " : "Published. ", h("a", { href: href(["view", r.data.document_id, r.data.revision_id]) }, "Open it"), r.data.indexing_state === "ready" ? "" : " (search is still updating)"));
            }
            catch (err) {
                outcome.replaceChildren(notice("error", errorText(err)));
                submit.disabled = false;
            }
        } }, h("p", {}, h("label", { for: "file" }, "Generated document (HTML)"), input), preview, legacyBox, h("p", { class: "check" }, includeCode, h("label", { for: "include-code" }, "Include query code (M and SQL) in the shared library")), h("p", { class: "muted small" }, "Off by default. When off, query code is removed from the published document and the search index. " +
        "When on, the code is shared as written: obvious credentials are cleaned, but that cleaning is not a guarantee. " +
        "Older documents without a manifest always have query code withheld."), submit, outcome);
    mount(h("p", {}, h("a", { href: "#/" }, "← Library")), h("h1", {}, "Import documentation"), form);
}
async function detailsView(id) {
    const doc = await api(`/documents/${encodeURIComponent(id)}`);
    const d = doc.data;
    const revs = (await api(`/documents/${encodeURIComponent(id)}/revisions`)).data.items;
    const status = h("div", { "aria-live": "polite" });
    const toggle = async () => {
        const action = d.archived ? "restore" : "archive";
        if (!window.confirm(d.archived ? `Restore “${d.title}” to the library?` :
            `Archive “${d.title}”? It leaves the catalogue and search; its history is kept and it can be restored.`))
            return;
        try {
            await api(`/documents/${encodeURIComponent(id)}/${action}`, { method: "POST", headers: { "If-Match": doc.headers.get("ETag") || "" } });
            index = null;
            indexEtag = "";
            render();
        }
        catch (e) {
            status.replaceChildren(notice("error", errorText(e)));
        }
    };
    mount(h("p", {}, h("a", { href: "#/" }, "← Library")), h("h1", {}, d.title, " ", typeBadge(d.document_type), d.archived ? h("span", { class: "badge archived" }, "Archived") : null), d.description ? h("p", {}, d.description) : null, h("dl", { class: "kv" }, h("dt", {}, "Business area"), h("dd", {}, d.classification.business_area || "—"), h("dt", {}, "Environment"), h("dd", {}, d.classification.environment || "—"), h("dt", {}, "Owner"), h("dd", {}, d.classification.owner || "—"), h("dt", {}, "Tags"), h("dd", {}, d.tags.join(", ") || "—"), h("dt", {}, "Generated"), h("dd", {}, date(revs[0]?.generated_at)), h("dt", {}, "Published to the library"), h("dd", {}, date(revs[0]?.published_at))), h("p", { class: "actions" }, h("a", { class: "button", href: href(["view", d.document_id, d.current_revision_id]) }, "Open current version"), caps?.can_publish ? h("button", { type: "button", class: "secondary", onclick: toggle }, d.archived ? "Restore" : "Archive") : null), status, h("h2", {}, "Versions"), h("table", { class: "table" }, h("thead", {}, h("tr", {}, ...["Published", "Generated", "Publisher", "Size", ""].map((c) => h("th", { scope: "col" }, c)))), h("tbody", {}, ...revs.map((r) => h("tr", {}, h("td", {}, date(r.published_at), r.revision_id === d.current_revision_id ? h("span", { class: "badge" }, "Current") : null), h("td", {}, date(r.generated_at)), h("td", {}, r.publisher_subject || "—"), h("td", {}, `${Math.round(r.size_bytes / 1024)} KB`), h("td", {}, h("a", { href: href(["view", id, r.revision_id]) }, "Open"), " · ", h("a", { href: `${API}/documents/${encodeURIComponent(id)}/revisions/${encodeURIComponent(r.revision_id)}/download` }, "Download")))))));
}
const CONFIDENCE = { exact_static: "Exact (static evidence)", possible: "Possible",
    user_asserted: "Manual — user asserted" };
async function viewerView(id, rev, params) {
    const [doc, objs] = await Promise.all([
        api(`/documents/${encodeURIComponent(id)}`),
        api(`/documents/${encodeURIComponent(id)}/objects?revision_id=${encodeURIComponent(rev)}&limit=1000`)
    ]);
    const d = doc.data, objects = objs.data.items;
    const objectId = params.get("object"), generation = params.get("generation"), section = params.get("section");
    const status = h("div", { class: "viewer-status", "aria-live": "polite" });
    const frame = h("iframe", { title: `${d.title} (document)`, sandbox: "allow-scripts", referrerpolicy: "no-referrer",
        src: `${API}/documents/${encodeURIComponent(id)}/revisions/${encodeURIComponent(rev)}/view` });
    const panel = h("aside", { class: "related", "aria-label": "Related documentation" });
    const select = h("select", { "aria-label": "Select an object", onchange: (e) => {
            const v = e.target.value;
            location.hash = href(["view", id, rev], { object: v || null, generation });
        } }, h("option", { value: "" }, "Whole document"), ...objects.map((o) => h("option", { value: o.object_id, selected: o.object_id === objectId }, `${o.kind}: ${o.label}`)));
    const isCurrent = rev === d.current_revision_id;
    mount(h("nav", { class: "breadcrumbs", "aria-label": "Breadcrumbs" }, h("a", { href: "#/" }, "Library"), " › ", h("a", { href: href([d.document_type]) }, TYPE_LABEL[d.document_type]), " › ", h("a", { href: href(["documents", id]) }, d.title), " › ", isCurrent ? "Current version" : "Earlier version"), h("div", { class: "viewer-bar" }, select, h("a", { href: `${API}/documents/${encodeURIComponent(id)}/revisions/${encodeURIComponent(rev)}/download` }, "Download"), !isCurrent ? h("a", { href: href(["view", id, d.current_revision_id]) }, "Open current version") : null), status, h("div", { class: "viewer-layout" }, h("div", { class: "frame-wrap" }, frame), panel));
    const target = () => {
        const o = objectId ? objects.find((x) => x.object_id === objectId) : null;
        if (o?.view)
            return { oid: o.object_id, view: { view_id: o.view.view_id, args: o.view.args } };
        if (section) {
            const so = objects.find((x) => x.section_id === section && x.view);
            if (so?.view)
                return { oid: so.object_id, view: { view_id: so.view.view_id, args: so.view.args } };
            const sv = objs.data.sections.find((s) => s.section_id === section && s.view);
            if (sv?.view)
                return { oid: null, view: { view_id: sv.view.view_id, args: sv.view.args } };
        }
        return null;
    };
    const wanted = target();
    if ((objectId || section) && !wanted)
        status.replaceChildren(notice("warn", "This document has no view for that item; it opened at the start."));
    viewer = new ViewerChannel(frame, rev, () => {
        if (wanted)
            viewer?.navigate(wanted.oid, wanted.view);
    }, (n) => {
        if (!n.exact)
            status.replaceChildren(notice("warn", "The viewer opened the nearest section; the exact item was not found in this version."));
    });
    if (wanted)
        window.setTimeout(() => {
            if (viewer && !viewer.isReady)
                status.replaceChildren(notice("warn", "This document opened at the start; its viewer could not jump to the selected item."));
        }, 5000);
    await relatedPanel(panel, id, rev, objectId, generation, objects);
}
async function relatedPanel(panel, id, rev, objectId, generation, objects) {
    panel.replaceChildren(h("h2", {}, "Related documentation"), h("p", { class: "muted" }, "Loading…"));
    const q = new URLSearchParams({ revision_id: rev });
    if (generation)
        q.set("generation_id", generation);
    if (objectId)
        q.set("object_id", objectId);
    let rel;
    try {
        rel = (await api(`/documents/${encodeURIComponent(id)}/relationships?${q}`)).data;
    }
    catch (e) {
        panel.replaceChildren(h("h2", {}, "Related documentation"), notice("error", errorText(e)));
        return;
    }
    const g = rel.generation;
    const stateText = {
        ready: `Evidence computed ${date(g.computed_at)}.`,
        updating: `Updating relationships; showing evidence computed ${date(g.computed_at)}.`,
        pinned: `Evidence as computed ${date(g.computed_at)} for this version (not current evidence).`,
        evidence_unavailable: "No relationship evidence is available for this version."
    };
    const groups = [
        ["Upstream: pipelines that write this", rel.incoming.filter((l) => l.kind === "produces")],
        ["Downstream: reports that read this", rel.outgoing.filter((l) => l.kind === "produces")],
        ["Other readers", [...rel.incoming, ...rel.outgoing].filter((l) => l.kind === "reads")],
        ["Deletions", [...rel.incoming, ...rel.outgoing].filter((l) => l.kind === "deletes")],
        ["Manual links", rel.manual]
    ];
    const any = groups.some(([, ls]) => ls.length);
    panel.replaceChildren(...kids(h("h2", {}, "Related documentation"), h("p", { class: `muted state-${g.state}` }, stateText[g.state] || g.state), objectId ? h("p", { class: "muted" }, "Showing links for the selected object. ", h("a", { href: href(["view", id, rev], { generation }) }, "Show all")) : null, any ? null : h("p", {}, "No relationship found in the indexed documents."), ...groups.filter(([, ls]) => ls.length).map(([title, ls]) => h("section", {}, h("h3", {}, title), h("ul", { class: "links" }, ...ls.map((l) => linkRow(l, g.generation_id || null))))), caps?.can_manage_relationships && g.state !== "pinned" && g.state !== "evidence_unavailable"
        ? manualForm(id, rev, objectId, g.current_catalogue_sequence ?? 0, objects) : null));
}
function linkRow(l, generation) {
    const c = l.counterpart;
    const target = c.revision_id ? href(["view", c.document_id, c.revision_id], { object: c.object_id, generation })
        : href(["documents", c.document_id]);
    const ev = l.evidence || {};
    const notes = Array.isArray(ev.notes) ? ev.notes.map(String) : [];
    const endpoint = (e) => e && typeof e === "object"
        ? Object.entries(e).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k}: ${v}`).join(", ") : "";
    return h("li", {}, h("div", {}, typeBadge(c.document_type), " ", h("a", { href: target }, c.title), c.object_label || c.object_id ? h("span", { class: "muted" }, ` › ${c.object_label || c.object_id}`) : null), h("div", { class: "meta" }, h("span", { class: `badge conf-${l.confidence}` }, CONFIDENCE[l.confidence] || l.confidence), l.status && l.status !== "active" ? h("span", { class: "badge warn" }, l.status === "needs_review" ? "Needs review" : "Archived target") : null, c.environment ? ` ${c.environment}` : "", c.archived ? h("span", { class: "badge archived" }, "Archived") : null), l.origin === "manual"
        ? h("p", { class: "reason" }, `“${l.reason || ""}”`, l.creator_subject ? h("span", { class: "muted" }, ` — ${l.creator_subject}`) : null)
        : h("details", {}, h("summary", {}, "Evidence"), h("dl", { class: "kv small" }, h("dt", {}, "Pipeline side"), h("dd", {}, endpoint(ev.source_endpoint)), h("dt", {}, "Report side"), h("dd", {}, endpoint(ev.target_endpoint)), h("dt", {}, "Resolution"), h("dd", {}, `${ev.source_resolution || "?"} / ${ev.target_resolution || "?"}`), notes.length ? h("dt", {}, "Notes") : null, notes.length ? h("dd", {}, notes.join("; ")) : null)));
}
function manualForm(id, rev, objectId, sequence, objects) {
    const out = h("div", { "aria-live": "polite" });
    const targetDoc = h("select", { required: true, "aria-label": "Target document" }, h("option", { value: "" }, "Choose a document…"), ...(index?.documents || []).filter((d) => d.document_id !== id).map((d) => h("option", { value: `${d.document_id}|${d.revision_id}` }, `${TYPE_LABEL[d.document_type]}: ${d.title}`)));
    const targetObj = h("select", { "aria-label": "Target object" }, h("option", { value: "" }, "Whole document"));
    targetDoc.addEventListener("change", async () => {
        const [doc, r] = targetDoc.value.split("|");
        targetObj.replaceChildren(h("option", { value: "" }, "Whole document"));
        if (!doc)
            return;
        const res = await api(`/documents/${encodeURIComponent(doc)}/objects?revision_id=${encodeURIComponent(r)}&limit=1000`);
        targetObj.append(...res.data.items.map((o) => h("option", { value: o.object_id }, `${o.kind}: ${o.label}`)));
    });
    const kind = h("select", { "aria-label": "Relationship kind" }, ...["related_to", "produces", "consumes", "deletes"].map((k) => h("option", { value: k }, k.replace("_", " "))));
    const reason = h("textarea", { required: true, maxlength: "2000", "aria-label": "Reason", placeholder: "Why are these related?" });
    const source = objects.find((o) => o.object_id === objectId);
    return h("details", { class: "manual" }, h("summary", {}, "Add a manual link"), h("form", { onsubmit: async (e) => {
            e.preventDefault();
            const [doc, r] = targetDoc.value.split("|");
            try {
                await api("/relationships/manual", { method: "POST", json: {
                        source_document_id: id, source_revision_id: rev, source_object_id: source?.object_id || null,
                        target_document_id: doc, target_revision_id: r, target_object_id: targetObj.value || null,
                        expected_catalogue_sequence: sequence, kind: kind.value, reason: reason.value
                    } });
                render();
            }
            catch (err) {
                out.replaceChildren(notice("error", err instanceof ApiError && err.code === "SELECTION_STALE"
                    ? "The library changed since this page loaded. Reload and try again." : errorText(err)));
            }
        } }, h("p", { class: "muted small" }, source ? `From: ${source.kind} ${source.label}` : "From: this whole document"), targetDoc, targetObj, kind, reason, h("button", { type: "submit" }, "Add link"), out));
}
window.addEventListener("hashchange", () => { render(); });
render();
