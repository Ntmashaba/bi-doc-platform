"use strict";
/* Generator desktop UI (handoff section 4: start, prerequisites, batch review, processing,
   result, history). Everything is written with DOM nodes and textContent, never innerHTML.
   In the pywebview window, window.pywebview.api adds native pickers and a bridge-free
   preview window; in a plain browser the same UI works with typed paths. */
const SESSION = document.querySelector('meta[name="bidoc-session"]')?.content || "";
class ApiError extends Error {
    constructor(status, code, message) {
        super(message);
        this.status = status;
        this.code = code;
    }
}
async function api(path, method = "GET", json) {
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
        headers["X-Requested-With"] = "bidoc";
        headers["X-Bidoc-Session"] = SESSION;
    }
    if (json !== undefined)
        headers["Content-Type"] = "application/json";
    const res = await fetch(path, { method, headers, body: json === undefined ? undefined : JSON.stringify(json) });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
        const e = (data && data.error) || (data && data.detail ? { code: "INVALID_REQUEST", message: "Check the form." } : {});
        throw new ApiError(res.status, e.code || "HTTP_" + res.status, e.message || res.statusText);
    }
    return data;
}
function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === false || v === null)
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
const notice = (kind, ...c) => h("div", { class: `notice ${kind}`, role: kind === "error" ? "alert" : "status" }, ...c);
const main = () => document.getElementById("main");
function mount(...nodes) { main().replaceChildren(...nodes); }
const host = () => (window.pywebview?.api) || null;
const STATE_LABEL = { queued: "Queued", validating: "Validating", extracting: "Extracting",
    analysing: "Analysing", rendering: "Rendering", completed: "Completed", local_only: "Completed (local only)",
    failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" };
const KIND_LABEL = { abf: "Analysis Services Tabular backup", pbix: "Power BI PBIX", pbip: "Power BI project", tmdl: "Power BI model (TMDL)",
    bim: "Power BI model (.bim)", pbir: "Power BI report (PBIR)", extracted: "pbi-tools extract",
    adf_git: "Data Factory Git folder", adf_arm: "Data Factory ARM export", adf_resources: "Data Factory resource JSON" };
const ACTIVE = new Set(["queued", "validating", "extracting", "analysing", "rendering"]);
const RETRYABLE = new Set(["failed", "cancelled", "interrupted"]);
let timer;
function stopPolling() { if (timer !== undefined) {
    clearTimeout(timer);
    timer = undefined;
} }
// ---- start ----------------------------------------------------------------------
const form = { inputs: "", output_dir: "", profile: "local", include_query_code: false, environment: "",
    business_area: "", owner: "" };
async function startView() {
    const inputs = h("textarea", { id: "inputs", spellcheck: "false", placeholder: "One path per line" });
    inputs.value = form.inputs;
    const output = h("input", { type: "text", id: "output", value: form.output_dir, placeholder: "Folder for the generated HTML" });
    const profile = h("select", { id: "profile" }, h("option", { value: "local" }, "Local (keeps everything, including query code)"), h("option", { value: "shared" }, "Shared (ready to publish to the library)"));
    profile.value = form.profile;
    const code = h("input", { type: "checkbox", id: "code" });
    code.checked = form.include_query_code;
    const fields = ["environment", "business_area", "owner"].map(k => h("input", { type: "text", id: k, value: form[k], maxlength: "200" }));
    const out = h("div", { id: "review", "aria-live": "polite" });
    inputs.addEventListener("input", () => stale());
    output.addEventListener("input", () => stale());
    const save = () => {
        Object.assign(form, { inputs: inputs.value, output_dir: output.value.trim(), profile: profile.value,
            include_query_code: code.checked, environment: fields[0].value, business_area: fields[1].value, owner: fields[2].value });
    };
    const paths = () => inputs.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const bridge = host();
    // A review is a snapshot of one list of inputs: changing the list, or where the output goes, withdraws it.
    const stale = () => {
        if (out.childElementCount)
            out.replaceChildren(h("p", { class: "muted", id: "review-stale" }, "The inputs or the output folder changed. Review the inputs again before generating."));
    };
    const add = (list) => { inputs.value = [...paths(), ...list].join("\n"); save(); stale(); };
    const review = async () => {
        save();
        out.replaceChildren();
        const reviewed = paths(); // what this review is of; Generate sends exactly this
        if (!paths().length) {
            out.append(notice("error", "Add at least one input."));
            return;
        }
        if (!form.output_dir) {
            out.append(notice("error", "Choose an output folder."));
            return;
        }
        let r;
        try {
            r = await api("/api/review", "POST", { inputs: reviewed, output_dir: form.output_dir });
        }
        catch (e) {
            out.append(notice("error", e.message));
            return;
        }
        const usable = r.items.filter(i => !i.errors?.length).length;
        const run = h("button", { class: "primary", disabled: usable === 0, onclick: async () => {
                run.disabled = true;
                try {
                    const b = await api("/api/batches", "POST", { inputs: reviewed, review_id: r.review_id, output_dir: form.output_dir,
                        profile: form.profile, include_query_code: form.include_query_code, environment: form.environment,
                        business_area: form.business_area, owner: form.owner });
                    location.hash = `#/batch/${b.batch_id}`;
                }
                catch (e) {
                    out.append(notice("error", e.message));
                    run.disabled = false;
                }
            } }, `Generate ${usable} of ${r.items.length}`);
        const sum = r.summary;
        out.append(h("h2", {}, "Review"), h("p", { id: "scan-summary" }, `Found ${sum.found} input${sum.found === 1 ? "" : "s"} in ${sum.selections} selection${sum.selections === 1 ? "" : "s"}` +
            (sum.folders_scanned ? ` (${sum.folders_scanned} folder${sum.folders_scanned === 1 ? "" : "s"} searched)` : "") +
            (sum.problems ? `; ${sum.problems} problem${sum.problems === 1 ? "" : "s"} listed below` : "") + "."), ...r.warnings.map(w => h("p", { class: "muted" }, w)), h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Input"), h("th", {}, "Recognised as"), h("th", {}, "Notes"))), h("tbody", {}, ...r.items.map(i => h("tr", {}, h("td", {}, h("strong", {}, i.label), h("div", { class: "muted small" }, h("code", {}, i.source))), h("td", {}, i.kind ? KIND_LABEL[i.kind] || i.kind : "—"), h("td", {}, ...(i.errors || []).map(e => h("div", { class: "s-failed" }, e.message)), ...(i.warnings || []).map(w => h("div", { class: "muted" }, w))))))), h("p", { class: "muted small" }, "Folders are searched, project folders count as one input, and links are not followed. " +
            "This list is a snapshot: Generate queues exactly these inputs, not files added after the review. " +
            "Inputs that cannot be used are recorded as failed so you can fix and retry them."), run);
    };
    mount(h("h1", {}, "New batch"), h("div", { class: "card" }, h("label", { for: "inputs" }, "Inputs"), h("p", { class: "muted small" }, "PBIX files, complete PBIP project folders, TMDL or PBIR folders, model.bim, pbi-tools extracts, " +
        "Data Factory Git folders, ARM exports or resource JSON. Any other folder is searched, including its subfolders, for " +
        ".pbix and .abf files, .bim models and project folders. A .pbip file alone is only a pointer: choose its project folder."), inputs, bridge ? h("p", { class: "actions" }, h("button", { type: "button", onclick: async () => add(await bridge.pick_files()) }, "Add files…"), h("button", { type: "button", onclick: async () => add(await bridge.pick_folder()) }, "Add folder…")) : null, h("label", { for: "output" }, "Output folder"), output, bridge ? h("p", { class: "actions" }, h("button", { type: "button", onclick: async () => {
            const f = await bridge.pick_folder();
            if (f[0]) {
                output.value = f[0];
                save();
                stale();
            }
        } }, "Choose…")) : null, h("label", { for: "profile" }, "Output"), profile, h("p", { class: "check" }, code, h("label", { for: "code" }, "Shared output: include query code (M and SQL)")), h("p", { class: "muted small" }, "Off by default. When off, query code is removed from shared output and its search text. " +
        "When on, it is shared as written; obvious credentials are cleaned, but that is not a guarantee."), h("div", { class: "row" }, h("div", {}, h("label", { for: "environment" }, "Environment"), fields[0]), h("div", {}, h("label", { for: "business_area" }, "Business area"), fields[1]), h("div", {}, h("label", { for: "owner" }, "Owner"), fields[2])), h("p", {}, h("button", { class: "primary", type: "button", onclick: review }, "Review inputs"))), out);
}
// ---- batch ----------------------------------------------------------------------
function elapsed(it) {
    if (it.elapsed_seconds !== null)
        return `${it.elapsed_seconds.toFixed(1)} s`;
    if (it.started_at && ACTIVE.has(it.state))
        return `${((Date.now() - Date.parse(it.started_at)) / 1000).toFixed(0)} s`;
    return "";
}
async function batchView(id) {
    const table = h("tbody", {});
    const status = h("p", { id: "batch-status", "aria-live": "polite" });
    const backendNote = h("div", { id: "batch-backend" }); // the PBIX extractor, once for the whole batch
    const cancelAll = h("button", { onclick: async () => { await api(`/api/batches/${id}/cancel`, "POST"); await refresh(); } }, "Cancel remaining");
    const act = (label, fn) => h("button", { onclick: async () => {
            try {
                await fn();
            }
            catch (e) {
                status.replaceChildren(notice("error", e.message));
            }
            await refresh();
        } }, label);
    let connected = false;
    try {
        connected = (await api("/api/library")).state === "connected";
    }
    catch {
        connected = false;
    }
    const publish = (it) => h("button", { onclick: async (ev) => {
            const btn = ev.currentTarget;
            btn.disabled = true;
            try {
                const r = await api(`/api/items/${it.item_id}/publish`, "POST", {});
                status.replaceChildren(notice("info", `${r.status === "duplicate" ? "Already published" : "Published"}: ${r.title} ` +
                    `(${r.new_document ? "new document" : "new version"}) to ${r.library_url}.`));
            }
            catch (e) {
                status.replaceChildren(notice("error", e.message));
                btn.disabled = false;
            }
        } }, "Publish");
    async function refresh() {
        stopPolling();
        let b;
        try {
            b = await api(`/api/batches/${id}`);
        }
        catch (e) {
            mount(notice("error", e.message));
            return;
        }
        const running = b.items.some(i => ACTIVE.has(i.state));
        const done = b.items.filter(i => i.state === "completed" || i.state === "local_only").length;
        status.textContent = running ? `Working… ${done} of ${b.items.length} done.` : `Finished: ${done} of ${b.items.length} succeeded.`;
        cancelAll.disabled = !running;
        const pb = b.options.pbix_backend;
        backendNote.replaceChildren(...(pb && pb.fallback ? [notice("info", `PBIX extractor: ${pb.backend}. ${pb.fallback}`)] : []));
        table.replaceChildren(...b.items.map(it => h("tr", { "data-item": it.item_id }, h("td", {}, h("strong", {}, it.label), h("div", { class: "muted small" }, it.kind ? KIND_LABEL[it.kind] || it.kind : "")), h("td", { class: `state s-${it.state}` }, STATE_LABEL[it.state] || it.state), h("td", {}, elapsed(it)), h("td", {}, ...it.errors.map(e => h("div", { class: "s-failed" }, e.message)), ...it.warnings.map(w => h("div", { class: "muted small" }, w)), it.artifact_path ? h("div", { class: "muted small" }, h("code", {}, it.artifact_path)) : null), h("td", { class: "actions" }, it.artifact_path && (it.state === "completed" || it.state === "local_only")
            ? h("button", { onclick: () => openDoc(it.item_id) }, "Open") : null, connected && it.state === "completed" && b.options.profile === "shared" ? publish(it) : null, ACTIVE.has(it.state) ? act("Cancel", () => api(`/api/items/${it.item_id}/cancel`, "POST")) : null, RETRYABLE.has(it.state) ? act("Retry", () => api(`/api/items/${it.item_id}/retry`, "POST")) : null))));
        if (running)
            timer = window.setTimeout(refresh, 1000);
    }
    mount(h("p", {}, h("a", { href: "#/history" }, "← History")), h("h1", {}, "Batch"), status, backendNote, h("div", { class: "card" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Input"), h("th", {}, "State"), h("th", {}, "Time"), h("th", {}, "Details"), h("th", {}, ""))), table), h("p", {}, cancelAll)), h("div", { id: "preview" }));
    await refresh();
}
async function openDoc(itemId) {
    const bridge = host();
    if (bridge) {
        await bridge.open_preview(itemId);
        return;
    }
    // Browser: an isolated frame (the response itself carries the sandbox CSP).
    const frame = h("iframe", { src: `/api/items/${itemId}/view`, sandbox: "allow-scripts", title: "Documentation preview",
        class: "preview-frame" });
    document.getElementById("preview")?.replaceChildren(frame);
}
async function libraryView() {
    const status = h("div", { id: "library-status", "aria-live": "polite" });
    const url = h("input", { type: "text", id: "library-url", placeholder: "https://docs.example.com" });
    const token = h("input", { type: "password", id: "library-token", autocomplete: "off" });
    const show = (s) => {
        const text = {
            connected: `Connected to ${s.url} as ${s.subject}.`, not_connected: "Not connected.",
            credential_rejected: `The library at ${s.url} no longer accepts the stored token (expired or revoked). Enter a new one.`,
            unreachable: `Could not reach ${s.url}: ${s.message || ""}`
        };
        status.replaceChildren(notice(s.state === "connected" ? "info" : s.state === "not_connected" ? "info" : "warn", text[s.state]), s.token_store ? h("p", { class: "muted small" }, `Tokens are kept in ${s.token_store}; never in documents or logs.`) : "", s.url ? h("p", {}, h("button", { type: "button", onclick: async () => { show(await api("/api/library", "DELETE")); } }, "Disconnect")) : "");
        if (s.url)
            url.value = s.url;
    };
    const connect = h("button", { class: "primary", type: "button", onclick: async () => {
            connect.disabled = true;
            try {
                show(await api("/api/library", "POST", { url: url.value.trim(), token: token.value.trim() }));
                token.value = "";
            }
            catch (e) {
                status.replaceChildren(notice("error", e.message));
            }
            connect.disabled = false;
        } }, "Connect");
    mount(h("h1", {}, "Library"), h("p", { class: "muted" }, "Publish generated documents straight to your team's library. Create a publishing token in the " +
        "library (Publishing tokens), then paste it here. It is valid for 1 to 30 days and can be revoked there at any time."), status, h("div", { class: "card" }, h("label", { for: "library-url" }, "Library address"), url, h("label", { for: "library-token" }, "Publishing token"), token, h("p", { class: "muted small" }, "Only https:// addresses are accepted (http:// only for this computer)."), h("p", {}, connect)));
    show(await api("/api/library"));
}
// ---- history and prerequisites --------------------------------------------------------
async function historyView() {
    const [batches, state] = await Promise.all([api("/api/batches"), api("/api/state")]);
    mount(h("h1", {}, "History"), state.interrupted_on_start ? notice("warn", `${state.interrupted_on_start} item(s) were still running when the generator last closed. ` +
        "They are marked Interrupted; open their batch to retry them.") : "", batches.length ? h("div", { class: "card" }, h("table", {}, h("tbody", {}, ...batches.map(b => {
        const ok = b.items.filter(i => i.state === "completed" || i.state === "local_only").length;
        return h("tr", {}, h("td", {}, h("a", { href: `#/batch/${b.batch_id}` }, new Date(b.created_at).toLocaleString())), h("td", {}, `${ok} of ${b.items.length} succeeded`), h("td", { class: "muted small" }, h("code", {}, b.output_dir)));
    })))) : h("p", { class: "muted" }, "No batches yet."));
}
async function prerequisitesView() {
    const d = await api("/api/doctor");
    mount(h("h1", {}, "Prerequisites"), h("p", { class: "muted" }, d.platform), h("div", { class: "card" }, h("table", {}, h("tbody", {}, ...d.checks.map(c => h("tr", {}, h("td", {}, c.check), h("td", { class: c.ok ? "s-completed" : "s-failed" }, c.ok ? "Found" : "Missing"), h("td", {}, c.detail, c.fix ? h("div", { class: "muted small" }, c.fix) : null)))))), h("h2", {}, "What this machine can document"), h("div", { class: "card" }, h("table", {}, h("tbody", {}, ...Object.entries(d.inputs).map(([k, r]) => h("tr", {}, h("td", {}, KIND_LABEL[k] || k), h("td", { class: r.available ? "s-completed" : "s-failed" }, r.available ? "Ready" : "Unavailable"), h("td", { class: "muted small" }, r.reason || "")))))), h("p", { class: "muted small" }, "PBIP, model and Data Factory inputs never need Power BI Desktop or pbi-tools."));
}
async function route() {
    stopPolling();
    const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
    try {
        if (parts[0] === "batch" && parts[1])
            await batchView(parts[1]);
        else if (parts[0] === "history")
            await historyView();
        else if (parts[0] === "prerequisites")
            await prerequisitesView();
        else if (parts[0] === "library")
            await libraryView();
        else
            await startView();
    }
    catch (e) {
        mount(notice("error", e instanceof ApiError ? e.message : "Something went wrong. Try again."));
    }
    main().focus();
}
window.addEventListener("hashchange", () => void route());
void route();
