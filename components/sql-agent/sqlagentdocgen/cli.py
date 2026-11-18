"""Command line for the SQL Agent engine.

  python -m sqlagentdocgen <file.sql|folder> [--coverage-only] [-o out.json]

--coverage-only prints only aggregate coverage counts and warning texts —
never job names, command text, or file content — so real job scripts can be
validated without anything sensitive leaving the user's machine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analyzer import analyse
from .loader import load_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="sqlagentdocgen",
        description="Static analysis of scripted SQL Server Agent jobs "
                    "(never connects to a server).",
    )
    parser.add_argument("input", help="A scripted .sql job file or a folder of them")
    parser.add_argument(
        "--coverage-only",
        action="store_true",
        help="print only coverage counts and warnings (safe to share)",
    )
    parser.add_argument("-o", "--output", help="write the JSON payload here")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    target = Path(args.input)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return 2

    payload = analyse(load_path(target))

    if args.coverage_only:
        cov = payload["coverage"]
        print(f"input files analysed : {cov['inputFiles']}")
        print(f"files skipped        : {len(cov['skippedFiles'])}")
        print(f"unrecognised EXECs   : {cov['unrecognisedStatements']}")
        print(f"steps not parsed     : {cov['unparsedSteps']}")
        print(f"unknown operations   : {cov['unknownOperations']}")
        print(f"jobs found           : {len(payload['jobs'])}")
        print(f"issues raised        : {len(payload['issues'])}")
        for warning in payload["warnings"]:
            print(f"warning: {warning}")
        return 0

    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
