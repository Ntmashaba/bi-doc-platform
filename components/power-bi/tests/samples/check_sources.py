"""Check source detection on real Power BI projects.

Usage:
    python tests/samples/check_sources.py FOLDER [FOLDER ...]

Finds every *.SemanticModel folder (PBIP/TMDL or model.bim) under the given
folders and prints, per partition, the source shown on Overview/Lineage and
whether the Sources view (the M tracer) agrees. Ends with totals: partitions,
"Unknown" sources, and disagreements. Nothing is written or uploaded.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.project import discover  # noqa: E402
from pbidocgen.source_objects import build_source_objects  # noqa: E402


def check(model_dir, totals, verbose):
    model = parse_model(discover(model_dir)["model"])
    traced = {}
    for row in build_source_objects(model, None, None):
        traced.setdefault((row["table"], row["partition"]), set()).add(row["sourceType"])
    print(f"\n{model_dir}")
    for table in model["tables"]:
        for part in table["partitions"]:
            source = part["source"]
            agree = source["sourceType"] in traced.get((table["name"], part["name"]), {source["sourceType"]})
            totals["partitions"] += 1
            totals["unknown"] += source["sourceType"] == "Unknown"
            totals["disagree"] += not agree
            if verbose or not agree or source["sourceType"] == "Unknown":
                flag = "" if agree else "   <- Sources view says " + ", ".join(sorted(traced[(table["name"], part["name"])]))
                print(f"  {table['name'][:40]:40} {source.get('label') or source['sourceType']}{flag}")


def main(folders):
    verbose = "--all" in folders
    totals = Counter()
    for folder in (f for f in folders if f != "--all"):
        for model_dir in sorted(p for p in Path(folder).rglob("*.SemanticModel") if ".git" not in p.parts):
            try:
                check(model_dir, totals, verbose)
            except Exception as exc:  # report and continue with the next project
                totals["failed"] += 1
                print(f"\n{model_dir}\n  FAILED: {exc}")
    print(f"\n{totals['partitions']} partitions, {totals['unknown']} Unknown, "
          f"{totals['disagree']} where Overview and Sources disagree, {totals['failed']} projects failed")
    return 1 if totals["disagree"] or totals["failed"] else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
