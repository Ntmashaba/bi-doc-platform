"""Check the SQL Agent engine against public, pinned job scripts (samples/sql-agent-corpus.json).

    python scripts/sql_agent_corpus.py            # fetch, verify checksums, compare counters; exit 1 on any difference
    python scripts/sql_agent_corpus.py --json     # the same, as one JSON report on stdout

Nothing from these scripts is committed: the manifest holds each file's source URL at a pinned commit, its SHA-256,
its license and the counters the engine is expected to report. Downloads go to samples/downloads/sql-agent/ (ignored by
git) and are refused unless their bytes match the manifest. The report prints counts only, never job names, step
names or command text, so it is safe to paste into an issue or a pull request.

The corpus is evidence about parser coverage, not a test input: continuous integration does not run this script
(the downloads are third-party files). Run it by hand after a change to the tokenizer, parser or registry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "samples" / "sql-agent-corpus.json"
CACHE = ROOT / "samples" / "downloads" / "sql-agent"
LIMIT = 10 * 1024 * 1024
COUNTERS = ("inputFiles", "skippedFiles", "jobs", "steps", "unparsedSteps", "unrecognisedStatements",
            "unknownOperations", "warnings")


def fetch(item: dict) -> Path:
    """The verified local copy of one manifest entry, downloading it once."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / item["file"]
    if not path.exists():
        request = urllib.request.Request(item["url"], headers={"User-Agent": "bi-doc-platform-sql-agent-corpus"})
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError(f"{item['id']}: larger than the {LIMIT}-byte limit")
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError(f"{item['id']}: checksum mismatch on download")
        path.write_bytes(raw)
    if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
        raise ValueError(f"{item['id']}: checksum mismatch ({path} changed; delete it to fetch again)")
    return path


def counters(path: Path) -> dict:
    """The engine's coverage counters for one file: counts only, nothing from the script itself."""
    from sqlagentdocgen.analyzer import analyse  # noqa: PLC0415 - optional until the engine is installed
    from sqlagentdocgen.loader import load_path  # noqa: PLC0415

    analysis = analyse(load_path(path))
    cov = analysis["coverage"]
    return {
        "inputFiles": cov.get("inputFiles", 0),
        "skippedFiles": len(cov.get("skippedFiles", [])),           # a list of paths: count it, never print it
        "jobs": len(analysis["jobs"]),
        "steps": sum(len(job.get("steps", [])) for job in analysis["jobs"]),
        "unparsedSteps": cov.get("unparsedSteps", 0),
        "unrecognisedStatements": cov.get("unrecognisedStatements", 0),
        "unknownOperations": cov.get("unknownOperations", 0),
        "warnings": len(analysis["warnings"]),
    }


def run() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    results = []
    for item in manifest["files"]:
        actual = counters(fetch(item))
        expected = {key: item["expected"][key] for key in COUNTERS}
        results.append({"id": item["id"], "group": item["group"], "expected": expected, "actual": actual,
                        "ok": actual == expected})
    return {"ok": all(r["ok"] for r in results), "files": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="print one JSON report instead of a table")
    args = parser.parse_args(argv)
    report = run()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        width = max(len(r["id"]) for r in report["files"])
        print(f"{'id':<{width}}  group      " + " ".join(f"{c:>6.6}" for c in COUNTERS))
        for r in report["files"]:
            print(f"{r['id']:<{width}}  {r['group']:<9}  " + " ".join(f"{r['actual'][c]:>6}" for c in COUNTERS)
                  + ("" if r["ok"] else "   <- differs"))
            if not r["ok"]:
                for c in COUNTERS:
                    if r["actual"][c] != r["expected"][c]:
                        print(f"    {c}: expected {r['expected'][c]}, got {r['actual'][c]}")
        print("corpus matches the manifest" if report["ok"] else "corpus DIFFERS from the manifest: investigate first")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
