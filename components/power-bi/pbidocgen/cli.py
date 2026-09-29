#!/usr/bin/env python3
"""Generate a self-contained HTML documentation file for a Power BI
semantic model, a PBIR report folder, or both together.

The semantic model may be TMSL (model.bim) or TMDL (a .SemanticModel folder of
.tmdl files) — Power BI Desktop writes TMDL by default when you save a project.

Examples
--------
A whole project, both artifacts discovered automatically:
    python generate_docs.py --project Sales.pbip --output docs/Sales.html
    python generate_docs.py --project C:/GIT/Sales --output docs/Sales.html

Point at either artifact folder; the other is found via definition.pbir:
    python generate_docs.py --project Sales.Report --output docs/Sales.html

Naming both explicitly (TMDL folder or .bim, both accepted):
    python generate_docs.py --model Sales.SemanticModel \
                            --report Sales.Report \
                            --output docs/Sales.html --title "Sales"

Semantic model only:
    python generate_docs.py --model model.bim --output docs/model.html

Report only (requirements manifest):
    python generate_docs.py --report Sales.Report --output docs/report.html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pbidocgen.model_parser import parse_model
from pbidocgen.report_parser import parse_report
from pbidocgen.linker import link
from pbidocgen.renderer import build_payload, render_html


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Power BI documentation generator (semantic model / PBIR report / combined).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--project", help="Path to a .pbip file, a project folder, or "
                                      "either artifact folder — the semantic model and "
                                      "report are discovered from it")
    ap.add_argument("--model", help="Path to a semantic model: a .SemanticModel folder "
                                    "(TMDL or TMSL) or a model.bim file")
    ap.add_argument("--report", help="Path to a PBIR report folder (the *.Report folder)")
    ap.add_argument("--output", "-o", default=None, help="Output HTML path (default: <name>.html)")
    ap.add_argument("--title", default=None, help="Document title (default: derived from inputs)")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="Also write the consolidated analysis JSON to this path")
    ap.add_argument("--csv", dest="csv_out", metavar="PATH",
                    help="Write column usage CSV: one row per column and page (requires a model)")
    ap.add_argument("--word", dest="word_out", default=None,
                    help="Also write a Word document (.docx) to this path — the narrative "
                         "subset for handovers and sign-off; the HTML stays the working doc")
    ap.add_argument("--agent", dest="agent_out", nargs="?", const="", default=None,
                    metavar="PATH",
                    help="Also write an agent context document (.agent.md) — the compact "
                         "markdown distillation for LLM agents. With no PATH, writes next "
                         "to the HTML output")
    ap.add_argument("--catalog", metavar="FOLDER", help="Build or refresh pbi-home.html from existing HTML files; no model required")
    ap.add_argument("--metadata", metavar="JSON", help="Apply report location and username references from a documentation JSON file")
    ap.add_argument("--no-hub", action="store_true", help="Do not refresh the documentation home after generation")
    pbix_inputs = ap.add_mutually_exclusive_group()
    pbix_inputs.add_argument("--pbix-folder", metavar="FOLDER", help="Generate documentation for every PBIX in a folder")
    pbix_inputs.add_argument("--pbix", metavar="FILE", help="Generate documentation for one PBIX")
    ap.add_argument("--output-dir", metavar="FOLDER", help="PBIX HTML destination (default: input folder/documentation)")
    ap.add_argument("--recursive", action="store_true", help="Include PBIX files in subfolders")
    ap.add_argument("--pbi-tools", metavar="EXE", help="Path to the pbi-tools Desktop executable")
    ap.add_argument("--pbixray", action="store_true",
                    help="Extract PBIX files with pbixray instead of pbi-tools (any OS; approximate; needs pip install pbixray)")
    ap.add_argument("--extract-timeout", type=int, default=600, metavar="SECONDS", help="Extraction timeout per PBIX (default: 600)")
    args = ap.parse_args(argv)
    if args.pbix_folder or args.pbix:
        # --model is allowed with --pbix only: it pairs a model with a live-connected report.
        conflicts = (args.project, args.report, args.output, args.title, args.json_out,
                     args.csv_out, args.word_out, args.agent_out is not None, args.metadata, args.catalog, args.no_hub)
        if any(conflicts) or (args.model and not args.pbix):
            ap.error("PBIX mode uses --output-dir and always creates a home page; do not combine it with project/report or individual export options.")
        from pbidocgen.pbix_batch import run_batch
        extractor = None
        if args.pbixray:
            from pbidocgen.pbixray_extract import extract_pbix as extractor
            if args.pbi_tools:
                ap.error("--pbixray and --pbi-tools are alternatives; use one")
        try:
            summary = run_batch(args.pbix_folder or args.pbix, args.output_dir,
                                args.recursive, args.pbi_tools, args.extract_timeout,
                                extractor=extractor, model_path=args.model)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 1 if summary['failed'] else 0
    if args.output_dir or args.recursive or args.pbi_tools or args.pbixray or args.extract_timeout != 600:
        ap.error("--output-dir, --recursive, --pbi-tools, --pbixray and --extract-timeout require --pbix or --pbix-folder")
    from pbidocgen.catalog import build_catalog, validate_metadata
    if args.catalog and not (args.model or args.report or args.project):
        if args.metadata:
            ap.error("--metadata requires a report/model generation input")
        try:
            print(f"Wrote {build_catalog(args.catalog)}")
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    if not args.model and not args.report and not args.project:
        ap.error("Provide --project, --model, --report, or a combination.")

    project_title = None
    if args.project:
        from pbidocgen.project import discover
        try:
            found = discover(args.project)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        project_title = found["title"]
        for note in found["notes"]:
            print(f"  note: {note}")
        # explicit flags win over discovery
        args.model = args.model or (str(found["model"]) if found["model"] else None)
        args.report = args.report or (str(found["report"]) if found["report"] else None)
        if not args.model and not args.report:
            print(f"error: nothing to document in {args.project}", file=sys.stderr)
            return 2

    model = report = linked = None
    if args.csv_out and not args.model:
        ap.error("--csv requires a semantic model; supply --model or --project.")

    if args.model:
        bim = Path(args.model)
        if not bim.exists():
            print(f"error: model file not found: {bim}", file=sys.stderr)
            return 2
        print(f"Parsing semantic model  {bim}")
        try:
            model = parse_model(bim)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"  {model['sourceFormat']}: {len(model['tables'])} tables, "
              f"{len(model['measures'])} measures, "
              f"{len(model['relationships'])} relationships")

    if args.report:
        rep = Path(args.report)
        if not rep.exists():
            print(f"error: report folder not found: {rep}", file=sys.stderr)
            return 2
        print(f"Parsing report folder   {rep}")
        try:
            report = parse_report(rep)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        n_vis = sum(len(p["visuals"]) for p in report["pages"])
        print(f"  {len(report['pages'])} pages, {n_vis} visuals, "
              f"{len(report['manifest'])} distinct field references")

    if model and report:
        print("Linking report to model")
        linked = link(model, report)
        verdicts = {}
        for u in linked["tableUsage"]:
            verdicts[u["usage"]] = verdicts.get(u["usage"], 0) + 1
        print("  usage verdicts: " + ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())))
        broken = [w for w in linked["warnings"] if w["category"] == "Broken binding"]
        if broken:
            print(f"  ⚠ {len(broken)} broken binding(s) — see the Warnings tab")

    title = args.title or (model["name"] if model else None) \
        or project_title or (report["name"] if report else "Power BI")
    payload = build_payload(model, report, linked, title)

    if args.metadata:
        try:
            payload['documentation'] = validate_metadata(json.loads(Path(args.metadata).read_text(encoding='utf-8-sig')))
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    out = args.output or f"{title}.html"
    try:
        path = render_html(payload, out)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {path}  ({path.stat().st_size/1024:.0f} KB, mode: {payload['mode']})")

    if args.json_out:
        jp = Path(args.json_out)
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {jp}")

    if args.csv_out:
        from pbidocgen.column_usage import write_column_csv
        cp = write_column_csv(payload["columns"], args.csv_out)
        print(f"Wrote {cp}  (column/page usage)")

    if args.word_out:
        from pbidocgen.word_writer import render_docx
        wp = render_docx(payload, args.word_out)
        print(f"Wrote {wp}  ({wp.stat().st_size/1024:.0f} KB, Word)")

    if args.agent_out is not None:
        from pbidocgen.agent_writer import render_agent_md, estimate_tokens
        apath = Path(args.agent_out) if args.agent_out else path.with_suffix(".agent.md")
        ap_ = render_agent_md(payload, apath)
        print(f"Wrote {ap_}  ({ap_.stat().st_size/1024:.0f} KB, "
              f"~{estimate_tokens(ap_.read_text(encoding='utf-8')):,} tokens, agent context)")
    if not args.no_hub:
        try:
            print(f"Wrote {build_catalog(args.catalog or path.parent)}")
        except (OSError, ValueError) as exc:
            print(f"error: report generated but home page could not be refreshed: {exc}", file=sys.stderr)
            return 2
    return 0


def cli() -> None:
    """Console-script entry point; same behaviour as running this file."""
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
