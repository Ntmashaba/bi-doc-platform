"""Generate local and shared artifacts for the 29 real reports pre-extracted in
pbi-tools/pbix-samples, validate every artifact, and check the shared ones hold no
query code while DAX and source locations survive.

    git clone https://github.com/pbi-tools/pbix-samples SAMPLES
    git -C SAMPLES checkout 84a442248b7259de7f5dbac0e25f0c33e3c9fd6a
    python packages/engines/tests/check_real_pbix_samples.py SAMPLES OUTPUT_DIR
"""
import re
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

from bidoc_contracts import validate_artifact
from bidoc_engines.generate import GenerateRequest, generate
from bidoc_engines.power_bi import VIEW_IDS

EXPECTED = 29
# Power Query over the same reports (measured 2026-10-08): every query of every report is read completely and
# its steps are parsed. A drop in these numbers means a reader or the step parser lost something.
MIN_QUERIES, MIN_STEPS, MIN_DESCRIBED = 179, 922, 0.90
# Page types as the 29 reports record them (measured 8 October 2026): every page's settings are in the file.
PAGE_TYPES = {"page": 218, "tooltip": 31, "drillthrough": 14}
MIN_BOOKMARK_PAGES = 51
# Queries that exist only in the Power Query package of a pre-2019 file (not loaded, read by no loaded query).
PACKAGE_ONLY = {"2019SU01 Blog Demo - February": {"FileLocation", "Order Details"}}
# A select statement as the document holds it: inside a JSON string a line break is the two characters \\n.
_GAP = r"(?:\s|\\[nrt])+"
CODE = [re.compile(p) for p in (r"let\\n\s+Source\s*=", r"Sql\.Database\(", r"Excel\.Workbook\(",
                                 r'Binary\.FromText\(\\?"[A-Za-z0-9+/=]{8}', r"Web\.Contents\(",
                                 r"(?i)\bselect" + _GAP + r"[^<]{0,200}?" + _GAP + r"from" + _GAP + r"[\w\[\]\".]+")]


def power_query(text):
    """The Power Query view embedded in a document: DERIVED.powerQuery."""
    import json
    return json.JSONDecoder().raw_decode(text, re.search(r"\bconst DERIVED = ", text).end())[0]["powerQuery"]


def main(samples, out_dir):
    folders = sorted(p for p in Path(samples, "powerbi-desktop-samples").glob("*/*") if (p / "Model").is_dir())
    failures, sizes, kept_dax = [], {"local": [], "shared": []}, 0
    totals = {"queries": 0, "steps": 0, "described": 0, "folders": 0}
    page_types, bookmark_pages = {}, 0
    with tempfile.TemporaryDirectory() as tmp:
        for folder in folders:
            work = Path(tmp) / folder.name
            shutil.copytree(folder, work)
            for profile in ("local", "shared"):
                r = generate(GenerateRequest(engine="power_bi", source_path=str(work), source_kind="extracted",
                                             output_dir=out_dir, profile=profile))
                if r.status != "completed":
                    failures.append(f"{folder.name} {profile}: {r.errors}")
                    continue
                data = Path(r.artifact_path).read_bytes()
                manifest = validate_artifact(data, view_ids=VIEW_IDS)
                sizes[profile].append((len(data), sum(len(s["text"].encode()) for s in manifest["sections"])))
                view = power_query(data.decode("utf-8"))
                if profile == "local":
                    report = manifest["native_payload"]["data"].get("report") or {}
                    for page in report.get("pages", []):
                        kind = page.get("pageType", "not recorded")
                        page_types[kind] = page_types.get(kind, 0) + 1
                    ids = {page["id"] for page in report.get("pages", [])}
                    bookmark_pages += sum(1 for b in report.get("bookmarks", []) if b.get("page") in ids)
                    names = [q["name"] for q in view["queries"]]
                    if len(names) != len(set(names)):
                        failures.append(f"{folder.name}: a query is listed twice: {sorted(n for n in names if names.count(n) > 1)}")
                    for q in view["queries"]:
                        if q["extraction"]["status"] != "complete" or q["steps"]["status"] not in ("parsed", "none") \
                                or q["publication"] != "included":
                            failures.append(f"{folder.name}: query {q['name']!r} is {q['extraction']['status']} / "
                                            f"{q['steps']['status']} / {q['publication']}: {q['steps'].get('note')}")
                        totals["steps"] += len(q["steps"].get("items", []))
                        totals["described"] += sum(1 for s in q["steps"].get("items", []) if s.get("says"))
                    totals["queries"] += len(names)
                    totals["folders"] += len(view["groups"])
                    missing = PACKAGE_ONLY.get(folder.name, set()) - set(names)
                    if missing:
                        failures.append(f"{folder.name}: queries held only by the Power Query package are missing: {sorted(missing)}")
                else:
                    for q in view["queries"]:
                        if q["publication"] != "withheld" or "items" in q["steps"]:
                            failures.append(f"{folder.name}: shared artifact shows steps or code of query {q['name']!r}")
                if profile == "shared":
                    text = data.decode("utf-8")
                    leaks = [p.pattern for p in CODE if p.search(text)]
                    if leaks:
                        failures.append(f"{folder.name}: query code in shared artifact: {leaks}")
                    kept = [t for t in manifest["native_payload"]["data"].get("tableSources", [])
                            if t.get("query") and t["query"] != "[query code withheld]"]
                    kept_dax += sum(1 for t in kept if t.get("queryKind") == "DAX")
                    # what a query is comes from its kind, not from how its text looks: only DAX may stay
                    shown = sorted({t.get("queryKind") or "no kind" for t in kept if t.get("queryKind") != "DAX"})
                    if shown:
                        failures.append(f"{folder.name}: shared artifact keeps {', '.join(shown)} query text")
    for profile, s in sizes.items():
        if s:
            print(f"{profile}: {len(s)} artifacts; HTML median {statistics.median(x for x, _ in s):,.0f} B, "
                  f"max {max(x for x, _ in s):,} B; search text median {statistics.median(y for _, y in s):,.0f} B, "
                  f"max {max(y for _, y in s):,} B")
    print(f"DAX calculated-table queries kept in shared artifacts: {kept_dax}")
    share = totals["described"] / totals["steps"] if totals["steps"] else 0
    print(f"Power Query: {totals['queries']} queries, {totals['folders']} query folders, {totals['steps']} Applied Steps, "
          f"{share:.0%} of them described in words")
    print("Pages: " + ", ".join(f"{n} {kind}" for kind, n in sorted(page_types.items())) +
          f"; {bookmark_pages} bookmarks open a page of their report")
    if page_types != PAGE_TYPES:
        failures.append(f"page types changed: {page_types}, measured {PAGE_TYPES}")
    if bookmark_pages < MIN_BOOKMARK_PAGES:
        failures.append(f"only {bookmark_pages} bookmarks name a page of their report; measured {MIN_BOOKMARK_PAGES}")
    if totals["queries"] < MIN_QUERIES or totals["steps"] < MIN_STEPS or share < MIN_DESCRIBED:
        failures.append(f"Power Query coverage fell below the measured floor ({MIN_QUERIES} queries, {MIN_STEPS} steps, "
                        f"{MIN_DESCRIBED:.0%} described)")
    if len(folders) < EXPECTED:
        failures.append(f"only {len(folders)} extracted reports found; expected {EXPECTED}")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"All {len(folders)} reports: valid local and shared artifacts; no query code in shared output.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "real-pbix-output")
