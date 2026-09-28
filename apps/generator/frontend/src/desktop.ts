/* Generator desktop UI (handoff section 4: start, prerequisites, batch review, processing,
   result, history). Everything is written with DOM nodes and textContent, never innerHTML.
   In the pywebview window, window.pywebview.api adds native pickers and a bridge-free
   preview window; in a plain browser the same UI works with typed paths. */

const SESSION = document.querySelector<HTMLMetaElement>('meta[name="bidoc-session"]')?.content || "";

interface Problem { code: string; message: string; }
interface Item { item_id: string; label: string; source: string; engine: string | null; kind: string | null;
  state: string; attempt: number; elapsed_seconds: number | null; started_at: string | null;
  artifact_path: string | null; warnings: string[]; errors: Problem[]; }
interface Batch { batch_id: string; created_at: string; output_dir: string; items: Item[];
  options: { profile: string; query_code: string }; }
interface ReviewItem { source: string; label: string; engine?: string; kind?: string; errors?: Problem[]; warnings?: string[]; }
interface Doctor { platform: string; checks: { check: string; ok: boolean; detail: string; fix: string | null }[];
  inputs: Record<string, { available: boolean; reason: string | null }>; }
interface HostApi { pick_files(): Promise<string[]>; pick_folder(): Promise<string[]>; open_preview(id: string): Promise<boolean>; }

class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

async function api<T>(path: string, method = "GET", json?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") { headers["X-Requested-With"] = "bidoc"; headers["X-Bidoc-Session"] = SESSION; }
  if (json !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: json === undefined ? undefined : JSON.stringify(json) });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = (data && data.error) || (data && data.detail ? { code: "INVALID_REQUEST", message: "Check the form." } : {});
    throw new ApiError(res.status, e.code || "HTTP_" + res.status, e.message || res.statusText);
  }
  return data as T;
}

type Child = Node | string | number | null | undefined | false;
function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, unknown> = {}, ...children: Child[]):
  HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === false || v === null) continue;
    if (typeof v === "function") el.addEventListener(k.replace(/^on/, ""), v as EventListener);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
const notice = (kind: string, ...c: Child[]) => h("div", { class: `notice ${kind}`, role: kind === "error" ? "alert" : "status" }, ...c);
const main = () => document.getElementById("main") as HTMLElement;
function mount(...nodes: (Node | string)[]) { main().replaceChildren(...nodes); }
const host = (): HostApi | null => ((window as unknown as { pywebview?: { api?: HostApi } }).pywebview?.api) || null;

const STATE_LABEL: Record<string, string> = { queued: "Queued", validating: "Validating", extracting: "Extracting",
  analysing: "Analysing", rendering: "Rendering", completed: "Completed", local_only: "Completed (local only)",
  failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted" };
const KIND_LABEL: Record<string, string> = { pbix: "Power BI PBIX", pbip: "Power BI project", tmdl: "Power BI model (TMDL)",
  bim: "Power BI model (.bim)", pbir: "Power BI report (PBIR)", extracted: "pbi-tools extract",
  adf_git: "Data Factory Git folder", adf_arm: "Data Factory ARM export", adf_resources: "Data Factory resource JSON" };
const ACTIVE = new Set(["queued", "validating", "extracting", "analysing", "rendering"]);
const RETRYABLE = new Set(["failed", "cancelled", "interrupted"]);

let timer: number | undefined;
function stopPolling() { if (timer !== undefined) { clearTimeout(timer); timer = undefined; } }

// ---- start ----------------------------------------------------------------------

const form = { inputs: "", output_dir: "", profile: "local", include_query_code: false, environment: "",
  business_area: "", owner: "" };

async function startView(): Promise<void> {
  const inputs = h("textarea", { id: "inputs", spellcheck: "false", placeholder: "One path per line" }) as HTMLTextAreaElement;
  inputs.value = form.inputs;
  const output = h("input", { type: "text", id: "output", value: form.output_dir, placeholder: "Folder for the generated HTML" }) as HTMLInputElement;
  const profile = h("select", { id: "profile" }, h("option", { value: "local" }, "Local (keeps everything, including query code)"),
    h("option", { value: "shared" }, "Shared (ready to publish to the library)")) as HTMLSelectElement;
  profile.value = form.profile;
  const code = h("input", { type: "checkbox", id: "code" }) as HTMLInputElement;
  code.checked = form.include_query_code;
  const fields = (["environment", "business_area", "owner"] as const).map(k =>
    h("input", { type: "text", id: k, value: form[k], maxlength: "200" }) as HTMLInputElement);
  const out = h("div", { id: "review", "aria-live": "polite" });
  const save = () => {
    Object.assign(form, { inputs: inputs.value, output_dir: output.value.trim(), profile: profile.value,
      include_query_code: code.checked, environment: fields[0].value, business_area: fields[1].value, owner: fields[2].value });
  };
  const paths = () => inputs.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const bridge = host();
  const add = (list: string[]) => { inputs.value = [...paths(), ...list].join("\n"); save(); };

  const review = async () => {
    save();
    out.replaceChildren();
    if (!paths().length) { out.append(notice("error", "Add at least one input.")); return; }
    if (!form.output_dir) { out.append(notice("error", "Choose an output folder.")); return; }
    let r: { items: ReviewItem[] };
    try { r = await api<{ items: ReviewItem[] }>("/api/review", "POST", { inputs: paths() }); }
    catch (e) { out.append(notice("error", (e as Error).message)); return; }
    const usable = r.items.filter(i => !i.errors?.length).length;
    const run = h("button", { class: "primary", disabled: usable === 0, onclick: async () => {
      run.disabled = true;
      try {
        const b = await api<Batch>("/api/batches", "POST", { inputs: paths(), output_dir: form.output_dir,
          profile: form.profile, include_query_code: form.include_query_code, environment: form.environment,
          business_area: form.business_area, owner: form.owner });
        location.hash = `#/batch/${b.batch_id}`;
      } catch (e) { out.append(notice("error", (e as Error).message)); run.disabled = false; }
    } }, `Generate ${usable} of ${r.items.length}`);
    out.append(h("h2", {}, "Review"), h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Input"), h("th", {}, "Recognised as"), h("th", {}, "Notes"))),
      h("tbody", {}, ...r.items.map(i => h("tr", {},
        h("td", {}, h("strong", {}, i.label), h("div", { class: "muted small" }, h("code", {}, i.source))),
        h("td", {}, i.kind ? KIND_LABEL[i.kind] || i.kind : "—"),
        h("td", {}, ...(i.errors || []).map(e => h("div", { class: "s-failed" }, e.message)),
          ...(i.warnings || []).map(w => h("div", { class: "muted" }, w))))))),
      h("p", { class: "muted small" }, "Inputs that cannot be used are recorded as failed so you can fix and retry them."), run);
  };

  mount(h("h1", {}, "New batch"),
    h("div", { class: "card" },
      h("label", { for: "inputs" }, "Inputs"),
      h("p", { class: "muted small" }, "PBIX files, complete PBIP project folders, TMDL or PBIR folders, model.bim, pbi-tools extracts, " +
        "Data Factory Git folders, ARM exports or resource JSON. A .pbip file alone is only a pointer: choose its project folder."),
      inputs,
      bridge ? h("p", { class: "actions" },
        h("button", { type: "button", onclick: async () => add(await bridge.pick_files()) }, "Add files…"),
        h("button", { type: "button", onclick: async () => add(await bridge.pick_folder()) }, "Add folder…")) : null,
      h("label", { for: "output" }, "Output folder"), output,
      bridge ? h("p", { class: "actions" }, h("button", { type: "button", onclick: async () => {
        const f = await bridge.pick_folder(); if (f[0]) { output.value = f[0]; save(); } } }, "Choose…")) : null,
      h("label", { for: "profile" }, "Output"), profile,
      h("p", { class: "check" }, code, h("label", { for: "code" }, "Shared output: include query code (M and SQL)")),
      h("p", { class: "muted small" }, "Off by default. When off, query code is removed from shared output and its search text. " +
        "When on, it is shared as written; obvious credentials are cleaned, but that is not a guarantee."),
      h("div", { class: "row" },
        h("div", {}, h("label", { for: "environment" }, "Environment"), fields[0]),
        h("div", {}, h("label", { for: "business_area" }, "Business area"), fields[1]),
        h("div", {}, h("label", { for: "owner" }, "Owner"), fields[2])),
      h("p", {}, h("button", { class: "primary", type: "button", onclick: review }, "Review inputs"))),
    out);
}

// ---- batch ----------------------------------------------------------------------

function elapsed(it: Item): string {
  if (it.elapsed_seconds !== null) return `${it.elapsed_seconds.toFixed(1)} s`;
  if (it.started_at && ACTIVE.has(it.state)) return `${((Date.now() - Date.parse(it.started_at)) / 1000).toFixed(0)} s`;
  return "";
}

async function batchView(id: string): Promise<void> {
  const table = h("tbody", {});
  const status = h("p", { id: "batch-status", "aria-live": "polite" });
  const cancelAll = h("button", { onclick: async () => { await api(`/api/batches/${id}/cancel`, "POST"); await refresh(); } }, "Cancel remaining");
  const act = (label: string, fn: () => Promise<unknown>) => h("button", { onclick: async () => {
    try { await fn(); } catch (e) { status.replaceChildren(notice("error", (e as Error).message)); }
    await refresh(); } }, label);

  async function refresh(): Promise<void> {
    stopPolling();
    let b: Batch;
    try { b = await api<Batch>(`/api/batches/${id}`); }
    catch (e) { mount(notice("error", (e as Error).message)); return; }
    const running = b.items.some(i => ACTIVE.has(i.state));
    const done = b.items.filter(i => i.state === "completed" || i.state === "local_only").length;
    status.textContent = running ? `Working… ${done} of ${b.items.length} done.` : `Finished: ${done} of ${b.items.length} succeeded.`;
    cancelAll.disabled = !running;
    table.replaceChildren(...b.items.map(it => h("tr", { "data-item": it.item_id },
      h("td", {}, h("strong", {}, it.label), h("div", { class: "muted small" }, it.kind ? KIND_LABEL[it.kind] || it.kind : "")),
      h("td", { class: `state s-${it.state}` }, STATE_LABEL[it.state] || it.state),
      h("td", {}, elapsed(it)),
      h("td", {}, ...it.errors.map(e => h("div", { class: "s-failed" }, e.message)),
        ...it.warnings.map(w => h("div", { class: "muted small" }, w)),
        it.artifact_path ? h("div", { class: "muted small" }, h("code", {}, it.artifact_path)) : null),
      h("td", { class: "actions" },
        it.artifact_path && (it.state === "completed" || it.state === "local_only")
          ? h("button", { onclick: () => openDoc(it.item_id) }, "Open") : null,
        ACTIVE.has(it.state) ? act("Cancel", () => api(`/api/items/${it.item_id}/cancel`, "POST")) : null,
        RETRYABLE.has(it.state) ? act("Retry", () => api(`/api/items/${it.item_id}/retry`, "POST")) : null))));
    if (running) timer = window.setTimeout(refresh, 1000);
  }
  mount(h("p", {}, h("a", { href: "#/history" }, "← History")), h("h1", {}, "Batch"), status,
    h("div", { class: "card" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Input"), h("th", {}, "State"),
      h("th", {}, "Time"), h("th", {}, "Details"), h("th", {}, ""))), table), h("p", {}, cancelAll)),
    h("div", { id: "preview" }));
  await refresh();
}

async function openDoc(itemId: string): Promise<void> {
  const bridge = host();
  if (bridge) { await bridge.open_preview(itemId); return; }
  // Browser: an isolated frame (the response itself carries the sandbox CSP).
  const frame = h("iframe", { src: `/api/items/${itemId}/view`, sandbox: "allow-scripts", title: "Documentation preview",
    class: "preview-frame" });
  document.getElementById("preview")?.replaceChildren(frame);
}

// ---- history and prerequisites --------------------------------------------------------

async function historyView(): Promise<void> {
  const [batches, state] = await Promise.all([api<Batch[]>("/api/batches"), api<{ interrupted_on_start: number }>("/api/state")]);
  mount(h("h1", {}, "History"),
    state.interrupted_on_start ? notice("warn", `${state.interrupted_on_start} item(s) were still running when the generator last closed. ` +
      "They are marked Interrupted; open their batch to retry them.") : "",
    batches.length ? h("div", { class: "card" }, h("table", {}, h("tbody", {}, ...batches.map(b => {
      const ok = b.items.filter(i => i.state === "completed" || i.state === "local_only").length;
      return h("tr", {}, h("td", {}, h("a", { href: `#/batch/${b.batch_id}` }, new Date(b.created_at).toLocaleString())),
        h("td", {}, `${ok} of ${b.items.length} succeeded`), h("td", { class: "muted small" }, h("code", {}, b.output_dir)));
    })))) : h("p", { class: "muted" }, "No batches yet."));
}

async function prerequisitesView(): Promise<void> {
  const d = await api<Doctor>("/api/doctor");
  mount(h("h1", {}, "Prerequisites"), h("p", { class: "muted" }, d.platform),
    h("div", { class: "card" }, h("table", {}, h("tbody", {}, ...d.checks.map(c => h("tr", {},
      h("td", {}, c.check), h("td", { class: c.ok ? "s-completed" : "s-failed" }, c.ok ? "Found" : "Missing"),
      h("td", {}, c.detail, c.fix ? h("div", { class: "muted small" }, c.fix) : null)))))),
    h("h2", {}, "What this machine can document"),
    h("div", { class: "card" }, h("table", {}, h("tbody", {}, ...Object.entries(d.inputs).map(([k, r]) => h("tr", {},
      h("td", {}, KIND_LABEL[k] || k), h("td", { class: r.available ? "s-completed" : "s-failed" }, r.available ? "Ready" : "Unavailable"),
      h("td", { class: "muted small" }, r.reason || "")))))),
    h("p", { class: "muted small" }, "PBIP, model and Data Factory inputs never need Power BI Desktop or pbi-tools."));
}

async function route(): Promise<void> {
  stopPolling();
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (parts[0] === "batch" && parts[1]) await batchView(parts[1]);
    else if (parts[0] === "history") await historyView();
    else if (parts[0] === "prerequisites") await prerequisitesView();
    else await startView();
  } catch (e) {
    mount(notice("error", e instanceof ApiError ? e.message : "Something went wrong. Try again."));
  }
  main().focus();
}

window.addEventListener("hashchange", () => void route());
void route();
