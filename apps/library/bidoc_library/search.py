"""Search semantics v1 (handoff section 4). The browser implementation (B06b) must match
this reference exactly; tests run both over the same cases.

- Case-insensitive AND of whitespace-separated terms, matched as substrings.
- A document matches when every term appears somewhere in its title, tags, section
  titles or section text.
- Rank: all terms in the title (tier 0), else in title + tags (tier 1), else anywhere
  (tier 2); ties by title (case-insensitive), then document ID.
- Each result points at the best section: most terms in its heading, then most terms in
  heading + text, then earliest. With no section holding any term it points at the
  title. The snippet is up to 160 characters around the first matching term. Snippets
  are plain text: renderers must escape them.
- Filters are exact: document_type, business_area, environment, owner, tag.
- A blank query returns the filtered catalogue in title order, without snippets.
"""
from __future__ import annotations

SNIPPET = 160
FILTERS = ("document_type", "business_area", "environment", "owner")


def terms_of(query: str | None) -> list[str]:
    return [t for t in (query or "").lower().split() if t]


def _snippet(text: str, terms) -> str:
    low = text.lower()
    hits = [low.find(t) for t in terms if low.find(t) >= 0]
    if not hits:
        return text[:SNIPPET]
    start = max(0, min(hits) - SNIPPET // 4)
    return ("…" if start else "") + text[start:start + SNIPPET] + ("…" if start + SNIPPET < len(text) else "")


def _matches_filters(doc: dict, filters: dict) -> bool:
    for key in FILTERS:
        value = filters.get(key)
        if value is not None:
            actual = doc["document_type"] if key == "document_type" else doc["classification"].get(key)
            if actual != value:
                return False
    tag = filters.get("tag")
    return tag is None or tag in doc["tags"]


def search(documents, query: str | None = None, **filters):
    terms = terms_of(query)
    results = []
    for doc in documents:
        if not _matches_filters(doc, filters):
            continue
        title, tags = doc["title"].lower(), " ".join(doc["tags"]).lower()
        if not terms:
            results.append((0, doc["title"].lower(), doc["document_id"], {
                "document_id": doc["document_id"], "revision_id": doc["revision_id"], "title": doc["title"],
                "document_type": doc["document_type"], "section_id": None, "section_title": None, "snippet": ""}))
            continue
        sections = [(s, s["title"].lower(), (s["title"] + "\n" + s["text"]).lower()) for s in doc["sections"]]
        everything = "\n".join([title, tags] + [text for _, _, text in sections])
        if not all(t in everything for t in terms):
            continue
        tier = 0 if all(t in title for t in terms) else 1 if all(t in title + "\n" + tags for t in terms) else 2
        best, best_score = None, (0, 0)
        for s, heading, text in sections:
            score = (sum(t in heading for t in terms), sum(t in text for t in terms))
            if score[1] and score > best_score:
                best, best_score = s, score
        results.append((tier, doc["title"].lower(), doc["document_id"], {
            "document_id": doc["document_id"], "revision_id": doc["revision_id"], "title": doc["title"],
            "document_type": doc["document_type"],
            "section_id": best["id"] if best else None, "section_title": best["title"] if best else None,
            "snippet": _snippet(best["title"] + " — " + best["text"], terms) if best else _snippet(doc["title"], terms)}))
    return [r[3] for r in sorted(results, key=lambda r: r[:3])]
