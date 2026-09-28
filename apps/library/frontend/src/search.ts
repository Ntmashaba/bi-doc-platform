/* Search semantics v1, a line-for-line port of bidoc_library/search.py.
   tests/test_frontend.py runs both implementations over the same cases. */

export interface Classification { business_area: string; environment: string; owner: string; }
export interface Section { id: string; title: string; text: string; }
export interface IndexedDocument {
  document_id: string; revision_id: string; document_type: string;
  classification: Classification; title: string; tags: string[]; sections: Section[];
}
export interface Filters {
  document_type?: string | null; business_area?: string | null; environment?: string | null;
  owner?: string | null; tag?: string | null;
}
export interface Hit {
  document_id: string; revision_id: string; title: string; document_type: string;
  section_id: string | null; section_title: string | null; snippet: string;
}

const SNIPPET = 160;
const FILTERS = ["document_type", "business_area", "environment", "owner"] as const;

export function termsOf(query: string | null | undefined): string[] {
  return (query || "").toLowerCase().split(/\s+/).filter((t) => t.length > 0);
}

function snippet(text: string, terms: string[]): string {
  const low = text.toLowerCase();
  const hits = terms.map((t) => low.indexOf(t)).filter((i) => i >= 0);
  if (!hits.length) return text.slice(0, SNIPPET);
  const start = Math.max(0, Math.min(...hits) - Math.floor(SNIPPET / 4));
  return (start ? "…" : "") + text.slice(start, start + SNIPPET) + (start + SNIPPET < text.length ? "…" : "");
}

function matchesFilters(doc: IndexedDocument, filters: Filters): boolean {
  for (const key of FILTERS) {
    const value = filters[key];
    if (value !== undefined && value !== null) {
      const actual = key === "document_type" ? doc.document_type : doc.classification[key];
      if (actual !== value) return false;
    }
  }
  const tag = filters.tag;
  return tag === undefined || tag === null || doc.tags.includes(tag);
}

function cmp(a: [number, string, string], b: [number, string, string]): number {
  for (let i = 0; i < 3; i++) {
    if (a[i] < b[i]) return -1;
    if (a[i] > b[i]) return 1;
  }
  return 0;
}

export function search(documents: IndexedDocument[], query?: string | null, filters: Filters = {}): Hit[] {
  const terms = termsOf(query);
  const results: Array<[[number, string, string], Hit]> = [];
  for (const doc of documents) {
    if (!matchesFilters(doc, filters)) continue;
    const title = doc.title.toLowerCase();
    const tags = doc.tags.join(" ").toLowerCase();
    const key: [number, string, string] = [0, title, doc.document_id];
    if (!terms.length) {
      results.push([key, { document_id: doc.document_id, revision_id: doc.revision_id, title: doc.title,
        document_type: doc.document_type, section_id: null, section_title: null, snippet: "" }]);
      continue;
    }
    const sections = doc.sections.map((s) => ({ s, heading: s.title.toLowerCase(), text: (s.title + "\n" + s.text).toLowerCase() }));
    const everything = [title, tags, ...sections.map((x) => x.text)].join("\n");
    if (!terms.every((t) => everything.includes(t))) continue;
    key[0] = terms.every((t) => title.includes(t)) ? 0 : terms.every((t) => (title + "\n" + tags).includes(t)) ? 1 : 2;
    let best: Section | null = null;
    let bestScore: [number, number] = [0, 0];
    for (const { s, heading, text } of sections) {
      const score: [number, number] = [terms.filter((t) => heading.includes(t)).length,
        terms.filter((t) => text.includes(t)).length];
      if (score[1] && (score[0] > bestScore[0] || (score[0] === bestScore[0] && score[1] > bestScore[1]))) {
        best = s; bestScore = score;
      }
    }
    results.push([key, { document_id: doc.document_id, revision_id: doc.revision_id, title: doc.title,
      document_type: doc.document_type, section_id: best ? best.id : null, section_title: best ? best.title : null,
      snippet: best ? snippet(best.title + " — " + best.text, terms) : snippet(doc.title, terms) }]);
  }
  return results.sort((a, b) => cmp(a[0], b[0])).map((r) => r[1]);
}
