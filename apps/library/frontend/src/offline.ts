/* Portable offline library (handoff 17.6; A12, A38).

   Opens from file:// with no network: every piece of data is in the page (a JSON script
   element), documents are sibling files shown in a sandboxed iframe, and object links use
   the same bi-doc-viewer protocol as the hosted library. The snapshot pins document
   revisions and one relationship generation; links to documents outside the export say
   "Not included". All text is written with textContent, never innerHTML. */
import { Hit, IndexedDocument, search } from "./search.js";
import { Navigated, ViewerChannel, ViewTarget } from "./protocol.js";

interface OfflineObject { object_id: string; label: string; kind: string; section_id: string;
  parent_object_id: string | null; view: ViewTarget | null; }
interface OfflineDocument extends IndexedDocument { file: string; objects: OfflineObject[];
  section_targets: { section_id: string; view: ViewTarget }[]; }
interface Link { relationship_id: string; kind: string; confidence: string; source_document_id: string;
  source_object_id: string; target_document_id: string; target_object_id: string; source_parent_object_id: string | null; }
interface Snapshot { format: string; title: string; created_at: string; generator_version: string; rule_version: string;
  documents: OfflineDocument[]; not_included: { document_id: string; title: string; document_type: string }[];
  relationships: Link[]; }

const SNAP: Snapshot = JSON.parse(document.getElementById("snapshot")?.textContent || "{}");
const DOCS = new Map(SNAP.documents.map((d) => [d.document_id, d]));
const OUTSIDE = new Map(SNAP.not_included.map((d) => [d.document_id, d]));
const TYPE_LABEL: Record<string, string> = { power_bi: "Power BI", adf: "Data Factory" };
let channel: ViewerChannel | null = null;

type Child = Node | string | null | undefined | false;
function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, unknown> = {}, ...children: Child[]):
  HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (typeof v === "function") el.addEventListener(k.replace(/^on/, ""), v as EventListener);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children) if (c) el.append(c);
  return el;
}
const main = () => document.getElementById("main") as HTMLElement;

function route(): { path: string[]; params: URLSearchParams } {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  return { path: path.split("/").filter(Boolean).map(decodeURIComponent), params: new URLSearchParams(query || "") };
}
function docHref(id: string, params: Record<string, string> = {}): string {
  const q = new URLSearchParams(params).toString();
  return `#/doc/${encodeURIComponent(id)}${q ? "?" + q : ""}`;
}

// ---- catalogue and search ----------------------------------------------------------

function home(params: URLSearchParams): void {
  const q = params.get("q") || "";
  const type = params.get("type") || "";
  const box = h("input", { type: "search", id: "q", value: q, "aria-label": "Search this export",
    placeholder: "Search titles, tables, measures, pipelines…" }) as HTMLInputElement;
  const results = h("section", { id: "results", "aria-live": "polite" });
  const show = () => {
    const hits: Hit[] = search(SNAP.documents, box.value, type ? { document_type: type } : {});
    results.replaceChildren(hits.length ? h("ol", { class: "hits" }, ...hits.map((hit) => h("li", {},
      h("span", { class: `badge ${hit.document_type}` }, TYPE_LABEL[hit.document_type] || hit.document_type),
      h("a", { href: docHref(hit.document_id, hit.section_id ? { section: hit.section_id } : {}) },
        hit.title + (hit.section_title ? ` › ${hit.section_title}` : "")),
      hit.snippet ? h("p", { class: "snippet" }, hit.snippet) : null))) : h("p", { class: "muted" }, "Nothing matches."));
  };
  box.addEventListener("input", show);
  main().replaceChildren(
    h("h1", {}, SNAP.title),
    h("p", { class: "muted small" }, `Offline snapshot made ${new Date(SNAP.created_at).toLocaleString()} ` +
      `(generator ${SNAP.generator_version}, relationship rules ${SNAP.rule_version}). It does not change or update itself.`),
    h("nav", { class: "types" }, ...[["", "All"], ["power_bi", "Power BI"], ["adf", "Data Factory"]].map(([t, label]) =>
      h("a", { href: `#/?type=${t}`, "aria-current": t === type ? "page" : undefined }, label))),
    box, results);
  show();
  box.focus();
}

// ---- document view with related objects --------------------------------------------------

function objectLabel(doc: OfflineDocument | undefined, id: string): string {
  return doc?.objects.find((o) => o.object_id === id)?.label || id;
}

function related(doc: OfflineDocument, objectId: string | null): HTMLElement {
  const mine = (l: Link, side: "source" | "target") =>
    l[`${side}_document_id`] === doc.document_id &&
    (!objectId || l[`${side}_object_id`] === objectId || (side === "source" && l.source_parent_object_id === objectId));
  const out = SNAP.relationships.filter((l) => mine(l, "source"));
  const inc = SNAP.relationships.filter((l) => mine(l, "target"));
  const item = (l: Link, outgoing: boolean) => {
    const otherId = outgoing ? l.target_document_id : l.source_document_id;
    const otherObj = outgoing ? l.target_object_id : l.source_object_id;
    const other = DOCS.get(otherId);
    const label = `${l.kind.replace(/_/g, " ")} · ${other ? other.title : OUTSIDE.get(otherId)?.title || otherId} › ` +
      objectLabel(other, otherObj);
    return h("li", {}, other
      ? h("a", { href: docHref(otherId, { object: otherObj }) }, label)
      : h("span", { class: "not-included" }, label, " — Not included in this export"),
      h("span", { class: "muted small" }, ` (${l.confidence.replace(/_/g, " ")})`));
  };
  return h("aside", { class: "related", "aria-label": "Related documentation" },
    h("h2", {}, objectId ? `Related to ${objectLabel(doc, objectId)}` : "Related documentation"),
    out.length || inc.length ? h("div", {},
      out.length ? h("h3", {}, "Uses or feeds") : null, out.length ? h("ul", {}, ...out.map((l) => item(l, true))) : null,
      inc.length ? h("h3", {}, "Used by") : null, inc.length ? h("ul", {}, ...inc.map((l) => item(l, false))) : null)
      : h("p", { class: "muted" }, "No links found in this snapshot."));
}

function view(id: string, params: URLSearchParams): void {
  const doc = DOCS.get(id);
  if (!doc) {
    main().replaceChildren(h("p", {}, h("a", { href: "#/" }, "← All documentation")),
      h("p", {}, OUTSIDE.has(id) ? `${OUTSIDE.get(id)?.title} is not included in this export.` : "Unknown document."));
    return;
  }
  const objectId = params.get("object");
  const sectionId = params.get("section");
  const status = h("p", { id: "nav-status", class: "muted small", "aria-live": "polite" });
  const frame = h("iframe", { src: doc.file, sandbox: "allow-scripts", title: doc.title, class: "viewer" }) as HTMLIFrameElement;
  const picker = h("select", { id: "object", "aria-label": "Go to object" },
    h("option", { value: "" }, "Whole document"),
    ...doc.objects.filter((o) => o.view).map((o) => h("option", { value: o.object_id, selected: o.object_id === objectId },
      `${o.kind}: ${o.label}`))) as HTMLSelectElement;
  picker.addEventListener("change", () => { location.hash = docHref(id, picker.value ? { object: picker.value } : {}); });
  main().replaceChildren(
    h("p", {}, h("a", { href: "#/" }, "← All documentation")),
    h("h1", {}, doc.title), h("div", { class: "toolbar" }, picker, status),
    h("div", { class: "split" }, frame, related(doc, objectId)));
  channel?.close();
  channel = new ViewerChannel(frame, doc.revision_id, () => {
    const obj = objectId ? doc.objects.find((o) => o.object_id === objectId) : null;
    const target = obj?.view || (sectionId ? doc.section_targets.find((t) => t.section_id === sectionId)?.view : null);
    if (target) channel?.navigate(obj ? obj.object_id : null, target);
  }, (n: Navigated) => {
    status.textContent = n.exact ? `Showing ${objectLabel(doc, n.object_id || "")}` : "Showing the nearest section";
  });
}

function render(): void {
  const r = route();
  if (r.path[0] === "doc" && r.path[1]) view(r.path[1], r.params);
  else { channel?.close(); channel = null; home(r.params); }
}

window.addEventListener("hashchange", render);
render();
