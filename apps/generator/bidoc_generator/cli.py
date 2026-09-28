"""bidoc: generate envelope-v1 documentation and diagnose prerequisites.

Exit codes: 0 success, 2 invalid input or configuration, 3 prerequisites missing,
4 generation failure, 130 cancelled. (5, partial batch failure, arrives with batches.)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading

from . import __version__
from .doctor import diagnose

EXIT_OK, EXIT_INPUT, EXIT_PREREQ, EXIT_FAILED, EXIT_CANCELLED = 0, 2, 3, 4, 130
KINDS = ("pbip", "tmdl", "bim", "pbir", "extracted", "adf_git", "adf_arm", "adf_resources")


def _parser():
    ap = argparse.ArgumentParser(prog="bidoc", description="BI Documentation Platform generator.")
    ap.add_argument("--version", action="version", version=f"bidoc {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="document one Power BI or Data Factory input",
                       description="Local output keeps everything, query code included. Shared output is "
                                   "projected for the shared library: machine paths removed, credentials and "
                                   "URL tokens cleaned (best effort), and query code withheld unless "
                                   "--include-query-code is given.")
    g.add_argument("--engine", required=True, choices=("power_bi", "adf"))
    g.add_argument("--source", required=True, help="PBIP project/folder, TMDL folder, model.bim, PBIR folder, pbi-tools extract folder, "
                                                   "or ADF Git folder / ARM export / resource JSON")
    g.add_argument("--kind", required=True, choices=KINDS, help="input kind")
    g.add_argument("--output-dir", required=True)
    g.add_argument("--profile", choices=("local", "shared"), default="local")
    g.add_argument("--include-query-code", action="store_true",
                   help="shared profile: publish M/SQL as written (cleaning is not a guarantee)")
    g.add_argument("--document-id", help="reuse this existing document identity")
    g.add_argument("--identity", choices=("existing", "new"),
                   help="for a moved or copied source: keep its identity or start a new one")
    g.add_argument("--title")
    g.add_argument("--description", default="")
    g.add_argument("--tag", action="append", default=[])
    g.add_argument("--environment", default="", help="e.g. Production; part of the document's identity")
    g.add_argument("--business-area", default="")
    g.add_argument("--owner", default="")
    g.add_argument("--json", action="store_true", help="print the result as JSON")

    d = sub.add_parser("doctor", help="report what this machine can generate")
    d.add_argument("--pbi-tools", help="path to pbi-tools.exe (otherwise BIDOC_PBI_TOOLS or PATH)")
    d.add_argument("--json", action="store_true")
    return ap


def _doctor(args) -> int:
    report = diagnose(args.pbi_tools)
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"bidoc {__version__} on {report['platform']}")
        for c in report["checks"]:
            print(f"  [{'ok' if c['ok'] else '--'}] {c['check']}: {c['detail']}" + (f"\n        fix: {c['fix']}" if c["fix"] else ""))
        for kind, r in report["inputs"].items():
            print(f"  {kind:14} {'ready' if r['available'] else 'unavailable: ' + r['reason']}")
    core = [c for c in report["checks"] if c["check"] in ("python", "pbi-doc-gen", "adf-doc-gen")]
    return EXIT_OK if all(c["ok"] for c in core) else EXIT_PREREQ


def _generate(args) -> int:
    try:
        from bidoc_engines.generate import GenerateRequest, generate  # noqa: PLC0415
    except ImportError as exc:
        print(f"error: prerequisites missing: {exc}. Run 'bidoc doctor'.", file=sys.stderr)
        return EXIT_PREREQ
    request = GenerateRequest(
        engine=args.engine, source_path=args.source, source_kind=args.kind, output_dir=args.output_dir,
        profile=args.profile, query_code="included" if args.include_query_code else "withheld",
        document_id=args.document_id, identity_choice=args.identity, title=args.title,
        description=args.description, tags=tuple(args.tag), environment=args.environment,
        business_area=args.business_area, owner=args.owner)
    if args.include_query_code and args.profile != "shared":
        print("note: local output always keeps query code; --include-query-code only affects --profile shared",
              file=sys.stderr)
    cancel = threading.Event()
    progress = (lambda stage, message="": None) if args.json else \
        (lambda stage, message="": print(f"  {stage}{': ' + message if message else ''}", file=sys.stderr))
    try:
        result = generate(request, progress, cancel)
    except KeyboardInterrupt:
        cancel.set()
        return EXIT_CANCELLED
    if args.json:
        print(json.dumps(result.__dict__, indent=1))
    else:
        if result.artifact_path:
            print(f"Wrote {result.artifact_path}")
        for w in result.warnings:
            print(f"  warning: {w}", file=sys.stderr)
        for e in result.errors:
            print(f"error: {e['message']}", file=sys.stderr)
    if result.status == "cancelled":
        return EXIT_CANCELLED
    if result.status == "completed" or (result.status == "local_only" and args.profile == "local"):
        return EXIT_OK
    codes = {e["code"] for e in result.errors}
    return EXIT_INPUT if codes & {"INVALID_INPUT", "IDENTITY_DECISION_REQUIRED"} else EXIT_FAILED


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    return _doctor(args) if args.command == "doctor" else _generate(args)


if __name__ == "__main__":
    sys.exit(main())
