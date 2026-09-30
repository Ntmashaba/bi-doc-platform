"""Same-file comparison of the two PBIX extractors: pbi-tools (Windows, needs Power BI Desktop) and the portable pbixray reader.

    python scripts/compare_extractors.py SAMPLE.pbix --pbi-tools C:\\path\\to\\pbi-tools.exe --report compare.json

Each extractor reads the same file; both extracts go through the same platform loader, and the resulting model facts are
compared category by category. Exit 0 = no differences, 1 = differences (listed in the report), 2 = could not run.
Differences are evidence to read, not an automatic verdict: `whitespace_only` marks expressions that differ only in
spacing. This shows parity for the files you compare, nothing more.
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("components/power-bi", "components/adf", "packages/contracts", "packages/engines", "packages/relationships",
            "apps/generator"):
    sys.path.insert(0, str(ROOT / sub))

CATEGORIES = ("tables", "columns", "measures", "relationships", "roles", "expressions", "sources")


def _squash(value):
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value


def facts(payload: dict) -> dict:
    """The model facts worth comparing, keyed so that two extracts line up. Volatile ids (lineage tags) are left out."""
    model = payload["model"]
    out = {c: {} for c in CATEGORIES}
    for t in model["tables"]:
        out["tables"][t["name"]] = {"isHidden": t.get("isHidden"), "dataCategory": t.get("dataCategory")}
        for c in t.get("columns", []):
            out["columns"][f'{t["name"]}[{c["name"]}]'] = {k: c.get(k) for k in (
                "dataType", "isHidden", "isCalculated", "expression", "sortByColumn", "formatString", "displayFolder")}
    for m in model["measures"]:
        out["measures"][f'{m["table"]}[{m["name"]}]'] = {k: m.get(k) for k in (
            "expression", "formatString", "displayFolder", "isHidden", "description")}
    for r in model["relationships"]:
        key = f'{r["fromTable"]}[{r["fromColumn"]}] -> {r["toTable"]}[{r["toColumn"]}]'
        out["relationships"][key] = {k: r.get(k) for k in (
            "isActive", "crossFilteringBehavior", "fromCardinality", "toCardinality")}
    for r in model.get("roles", []):
        out["roles"][r["name"]] = r
    for e in model.get("expressions", []):
        out["expressions"][e.get("name", "?")] = {k: v for k, v in e.items() if k != "lineageTag"}
    for src in payload.get("tableSources") or []:
        out["sources"][f'{src.get("table")}/{src.get("partition")}'] = {k: src.get(k) for k in (
            "server", "database", "sourceType", "objectType", "object", "queryKind", "query", "expression")}
    return out


def compare(a: dict, b: dict) -> dict:
    """Per category: keys only in a, only in b, and keys present in both whose facts differ."""
    report = {}
    for cat in CATEGORIES:
        left, right = a[cat], b[cat]
        changed = {}
        for key in sorted(left.keys() & right.keys()):
            if json.dumps(left[key], sort_keys=True, default=str) != json.dumps(right[key], sort_keys=True, default=str):
                whitespace_only = (json.dumps({k: _squash(v) for k, v in left[key].items()}, sort_keys=True, default=str)
                                   == json.dumps({k: _squash(v) for k, v in right[key].items()}, sort_keys=True, default=str)) \
                    if isinstance(left[key], dict) and isinstance(right[key], dict) else False
                changed[key] = {"pbi_tools": left[key], "pbixray": right[key], "whitespace_only": whitespace_only}
        report[cat] = {"count_pbi_tools": len(left), "count_pbixray": len(right),
                       "only_in_pbi_tools": sorted(left.keys() - right.keys()),
                       "only_in_pbixray": sorted(right.keys() - left.keys()), "changed": changed}
    return report


def differences(report: dict) -> int:
    return sum(len(v["only_in_pbi_tools"]) + len(v["only_in_pbixray"]) + len(v["changed"]) for v in report.values())


def extract_and_load(pbix: Path, tool: str, work: Path, timeout: float) -> dict:
    from bidoc_engines import power_bi
    from bidoc_generator.extract import extract_pbix
    extracted = extract_pbix(pbix, work, tool, timeout=timeout)
    return power_bi.load(extracted, "extracted", pbix.stem, pbix=pbix)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pbix", type=Path)
    ap.add_argument("--pbi-tools", required=True, help="path to pbi-tools.exe (Desktop edition)")
    ap.add_argument("--report", type=Path, default=Path("compare-report.json"))
    ap.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args(argv)
    try:
        from bidoc_generator import backend
        problem = backend.select_backend("pbi-tools", args.pbi_tools)
        if not problem.available:
            print(f"pbi-tools cannot be used: {problem.reason}", file=sys.stderr)
            return 2
        with tempfile.TemporaryDirectory() as d:
            side_a = facts(extract_and_load(args.pbix, args.pbi_tools, Path(d) / "pbi-tools", args.timeout))
            side_b = facts(extract_and_load(args.pbix, "pbixray", Path(d) / "pbixray", args.timeout))
    except Exception as exc:  # extraction failure is an inability to compare, not a difference
        print(f"comparison could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    report = compare(side_a, side_b)
    total = differences(report)
    args.report.write_text(json.dumps({"file": args.pbix.name, "differences": total, "categories": report}, indent=2,
                                      default=str), encoding="utf-8")
    for cat, v in report.items():
        print(f'{cat:14} pbi-tools {v["count_pbi_tools"]:4}  pbixray {v["count_pbixray"]:4}  '
              f'only-pbi-tools {len(v["only_in_pbi_tools"])}  only-pbixray {len(v["only_in_pbixray"])}  changed {len(v["changed"])}')
    print(f"{total} difference(s). Report: {args.report}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
