"""B01 legacy-import spike: recover the native payload from existing engine HTML
without executing it, and estimate the per-document searchable-text size.

Usage: python extract_native.py FILE.html [FILE.html ...]
"""
import json
import re
import sys
from pathlib import Path

MARKER = re.compile(r"\bconst DATA = ")
ENGINES = {"pbi-documentation-metadata": "power_bi", "adf-documentation-details": "adf"}


def extract(html: str):
    engine = next((v for k, v in ENGINES.items() if f'id="{k}"' in html), None)
    hits = list(MARKER.finditer(html))
    if engine is None or len(hits) != 1:
        raise ValueError(f"unsupported or ambiguous document (engine={engine}, DATA markers={len(hits)})")
    data, _ = json.JSONDecoder().raw_decode(html, hits[0].end())
    return engine, data


def names(engine, d):
    """Terms a user would search for; a lower bound on a useful index entry."""
    out = [d.get("title", "")]
    if engine == "power_bi":
        for t in (d.get("model") or {}).get("tables", []):
            out += [t["name"]] + [c["name"] for c in t.get("columns", [])] + [m["name"] for m in t.get("measures", [])]
        for p in (d.get("report") or {}).get("pages", []):
            out.append(p.get("name", ""))
        for s in d.get("sourceObjects", []):
            out += [s.get("server", ""), s.get("database", ""), s.get("object", "")]
    else:
        for p in d.get("pipelines", []):
            out += [p["name"], p.get("description") or ""] + [a["activity"] for a in p.get("activities", [])]
        out += [e.get("label", "") for e in d.get("entities", [])]
        out += [t.get("name", "") for t in d.get("triggers", [])]
    return " ".join(x for x in out if x)


if __name__ == "__main__":
    for f in sys.argv[1:]:
        engine, data = extract(Path(f).read_text(encoding="utf-8"))
        print(json.dumps({"file": Path(f).name, "engine": engine, "schemaVersion": data.get("schemaVersion"),
                          "payload_bytes": len(json.dumps(data).encode()),
                          "search_terms_bytes": len(names(engine, data).encode())}))
