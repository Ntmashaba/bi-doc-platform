"""Command line for the SQL Agent engine.

  python -m sqlagentdocgen <file.sql|folder> [--coverage-only] [-o out.json]
  python -m sqlagentdocgen <file.sql|folder> --html docs.html [--title "..."]
                             [--also json,word,csv,agent]

--coverage-only prints only aggregate coverage counts and warning texts —
never job names, command text, or file content — so real job scripts can be
validated without anything sensitive leaving the user's machine.

Without --html the payload is written as JSON (stdout, or -o).  With --html a
self-contained interactive HTML document is rendered; --also adds the extra
renderings (JSON payload, Word document, CSV inventories, agent markdown)
next to it.  Every rendered output passes through the redaction safety net.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__
from .analyzer import analyse
from .loader import load_path

EXTRAS = ("json", "word", "csv", "agent")


def write_csv(payload: dict, folder: Path) -> list:
    """Evidence inventories as CSV: jobs, steps, operations, schedules,
    issues, entities."""
    folder.mkdir(parents=True, exist_ok=True)
    job_rows = [
        [j.get("name"), j.get("identity"), j.get("guid"), j.get("enabled"),
         j.get("owner"), j.get("category"), len(j.get("steps", [])),
         "; ".join(s.get("name") or "" for s in j.get("schedules", [])),
         "; ".join(j.get("registeredOn", [])), j.get("sourceFile")]
        for j in payload.get("jobs", [])
    ]
    step_rows = [
        [j.get("name"), s.get("id"), s.get("name"), s.get("subsystem"),
         s.get("server") or s.get("database"), s.get("commandKind"),
         s.get("onSuccessAction"), s.get("onSuccessStepId"),
         s.get("onFailAction"), s.get("onFailStepId"),
         s.get("retryAttempts"), s.get("retryIntervalMinutes"),
         s.get("proxy"), len(s.get("operations", []))]
        for j in payload.get("jobs", []) for s in j.get("steps", [])
    ]
    op_rows = [
        [o.get("job"), o.get("step"), o.get("source"), o.get("kind"),
         o.get("verb"), o.get("object"), "; ".join(o.get("reads", [])),
         o.get("parallel"), o.get("transaction"), o.get("partitionGranular")]
        for o in payload.get("processing", {}).get("operations", [])
    ]
    sched_rows = [
        [j.get("name"), s.get("name"), s.get("summary"), s.get("enabled"),
         s.get("activeStartDate"), s.get("activeEndDate")]
        for j in payload.get("jobs", []) for s in j.get("schedules", [])
    ]
    issue_rows = [
        [i.get("severity"), i.get("code"), i.get("job"), i.get("step"),
         i.get("message"), i.get("evidence")]
        for i in payload.get("issues", [])
    ]
    entity_rows = [
        [e.get("key"), e.get("kind"),
         "; ".join(f"{k}={v}" for k, v in (e.get("endpoint") or {}).items() if v),
         "; ".join(f"{u.get('job')}/{u.get('step')}:{u.get('operation')}"
                   for u in e.get("usage", []))]
        for e in payload.get("entities", [])
    ]
    tables = {
        "jobs.csv": (["name", "identity", "guid", "enabled", "owner", "category",
                      "steps", "schedules", "registered on", "source file"], job_rows),
        "steps.csv": (["job", "step id", "step", "subsystem", "target", "command kind",
                       "on success action", "on success step", "on fail action",
                       "on fail step", "retry attempts", "retry interval (min)",
                       "proxy", "operations"], step_rows),
        "operations.csv": (["job", "step", "source", "kind", "type", "object",
                            "reads", "parallel", "transaction", "partition scope"],
                           op_rows),
        "schedules.csv": (["job", "schedule", "summary", "enabled",
                           "active start", "active end"], sched_rows),
        "issues.csv": (["severity", "code", "job", "step", "finding", "evidence"],
                       issue_rows),
        "entities.csv": (["key", "kind", "endpoint", "used by"], entity_rows),
    }
    written = []
    for name, (head, rows) in tables.items():
        path = folder / name
        # utf-8-sig so Excel opens names with accents correctly
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(head)
            w.writerows(rows)
        written.append(path)
    return written


def _default_title(target: Path) -> str:
    name = target.stem if target.is_file() else target.name
    return name.replace("_", " ").replace("-", " ").strip() or "SQL Agent jobs"


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
    parser.add_argument("--html", metavar="PATH",
                        help="render a self-contained interactive HTML document")
    parser.add_argument("--title", help="document title (default: input name)")
    parser.add_argument(
        "--also", metavar="LIST",
        help=f"with --html: also write these extras next to it "
             f"(comma-separated: {','.join(EXTRAS)})",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    target = Path(args.input)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return 2

    extras = []
    if args.also:
        extras = [e.strip().lower() for e in args.also.split(",") if e.strip()]
        bad = [e for e in extras if e not in EXTRAS]
        if bad:
            print(f"error: unknown --also value(s): {', '.join(bad)} "
                  f"(choose from {', '.join(EXTRAS)})", file=sys.stderr)
            return 2

    load = load_path(target)
    analysis = analyse(load)

    if args.coverage_only:
        cov = analysis["coverage"]
        print(f"input files analysed : {cov['inputFiles']}")
        print(f"files skipped        : {len(cov['skippedFiles'])}")
        print(f"unrecognised EXECs   : {cov['unrecognisedStatements']}")
        print(f"steps not parsed     : {cov['unparsedSteps']}")
        print(f"unknown operations   : {cov['unknownOperations']}")
        print(f"jobs found           : {len(analysis['jobs'])}")
        print(f"issues raised        : {len(analysis['issues'])}")
        # analyse() appends load errors after the registry warnings; in
        # coverage-only mode the load errors print in their sanitized public
        # form — no full paths, no raw OS error text.
        registry_warnings = analysis["warnings"][: len(analysis["warnings"]) - len(load.errors)]
        for warning in registry_warnings:
            print(f"warning: {warning}")
        for err in load.errors:
            print(f"warning: {err.public}")
        return 0

    if args.html or extras:
        from .renderer import build_payload, render_html
        payload = build_payload(analysis, args.title or _default_title(target))
        html_path = Path(args.html) if args.html else target.with_name(
            (target.stem if target.is_file() else target.name) + "_docs.html")
        render_html(payload, html_path)
        print(f"wrote {html_path}")
        if "json" in extras:
            jpath = html_path.with_suffix(".json")
            jpath.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
            print(f"wrote {jpath}")
        if "word" in extras:
            from .word_writer import render_docx
            dpath = html_path.with_suffix(".docx")
            render_docx(payload, dpath)
            print(f"wrote {dpath}")
        if "agent" in extras:
            from .agent_writer import estimate_tokens, render_agent_md
            apath = html_path.with_suffix(".agent.md")
            render_agent_md(payload, apath)
            print(f"wrote {apath}  "
                  f"(~{estimate_tokens(apath.read_text(encoding='utf-8')):,} tokens)")
        if "csv" in extras:
            for path in write_csv(payload, html_path.with_name(html_path.stem + "-csv")):
                print(f"wrote {path}")
        return 0

    # JSON payload mode (default): raw analysis, no rendering extras
    text = json.dumps(analysis, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
