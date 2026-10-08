"""What the Power Query view shows, worked out from the payload that is about to be rendered.

The payload's query inventory (`sourceQueries`) records the facts read from the file: what each query is, what
it feeds, how completely it was read. This module adds what can only be said about the copy being rendered:
whether the script is in it (publication), and the Applied Steps read from that script. Steps are therefore
never stored: a shared document that withholds query code has no step names either, and one that includes
cleaned code shows steps read from the cleaned text.

A payload written before the inventory recorded these facts is read again from its own model when the model
still holds the scripts; when it does not (a shared copy), the view says the facts were not recorded.
"""
from __future__ import annotations

from . import m_steps
from .object_index import query_id
from .publication import publication_of
from .source_queries import build_source_queries, query_groups

NOT_RECORDED = "not recorded"
_FACTS = ("objectId", "kind", "origin", "table", "partitions", "storageModes", "group", "order", "load", "resultType",
          "description", "usedBy", "upstream", "referencedBy", "sources", "alsoShared")


def _recorded(rows) -> bool:
    return any(isinstance(row, dict) and "extraction" in row for row in rows)


def _reread(payload: dict, rows: list[dict]) -> dict[int, dict]:
    """Facts for an older payload, read again from its model; {row position: facts}. Empty when scripts are withheld."""
    model = payload.get("model")
    if not isinstance(model, dict) or any(publication_of(row.get("mCode")) == "withheld" for row in rows):
        return {}
    try:
        fresh = build_source_queries(model, payload.get("report"))
    except (KeyError, TypeError, ValueError):
        return {}
    by_key = {}
    for row in fresh:
        by_key.setdefault((row["queryName"], row["mCode"]), row)
    return {i: by_key[key] for i, row in enumerate(rows)
            if (key := (row.get("queryName"), row.get("mCode") or "")) in by_key}


def power_query_view(payload: dict) -> dict:
    rows = [row for row in payload.get("sourceQueries") or [] if isinstance(row, dict)]
    recorded = _recorded(rows)
    reread = {} if recorded else _reread(payload, rows)
    # What each query name is, so a step that calls a function query or starts from another query can say so.
    kinds = {row.get("queryName"): (row if recorded else reread.get(i, {})).get("kind", "query")
             for i, row in enumerate(rows) if row.get("queryName")}
    queries, taken = [], set()
    renamed = {}             # an older payload read again: the id the fresh reading gave -> the id this document uses
    for i, row in enumerate(rows):
        facts = row if recorded else {k: v for k, v in reread.get(i, {}).items() if k != "objectId"}
        code = row.get("mCode") or ""
        publication = publication_of(code)
        entry = {"row": i, "name": row.get("queryName") or "", "publication": publication}
        for key in _FACTS:
            if facts.get(key) not in (None, "", []):
                entry[key] = facts[key]
        if "objectId" not in entry:                      # an older payload: the id the object index gives it
            base, n = query_id(entry["name"]), 2
            entry["objectId"] = base
            while entry["objectId"] in taken:
                entry["objectId"] = f"{base}~{n}"
                n += 1
        taken.add(entry["objectId"])
        if i in reread:
            renamed[reread[i]["objectId"]] = entry["objectId"]
        entry.setdefault("order", i)
        if "extraction" in facts:
            entry["extraction"] = facts["extraction"]
            steps = dict(facts["steps"])
        else:
            entry["extraction"] = {"status": NOT_RECORDED, "note": ""}
            steps = {"status": NOT_RECORDED, "note": "", "scope": ""}
        if publication != "withheld" and code.strip():
            # What is shown must match the script that is shown, so the steps come from this copy of it.
            reading = m_steps.read(code, kinds)
            at = m_steps.utf16_offsets(code)
            steps = {"status": reading.status, "note": reading.note, "scope": reading.scope, "items": [
                {key: value for key, value in (("name", step.name), ("s", at(step.start)), ("e", at(step.end)),
                                                ("call", step.call), ("says", step.description), ("note", step.comment))
                 if value not in ("", None)} for step in reading.steps]}
            if reading.returns:
                steps["returns"] = reading.returns
        entry["steps"] = steps
        queries.append(entry)
    if renamed:
        for entry in queries:
            for key in ("upstream", "referencedBy"):
                if key in entry:
                    entry[key] = [renamed[target] for target in entry[key] if target in renamed]
    return {"recorded": recorded or bool(reread), "queries": queries,
            "groups": query_groups(payload.get("model") if isinstance(payload.get("model"), dict) else None)}
