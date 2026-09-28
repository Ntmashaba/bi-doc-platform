"""bidoc: generate envelope-v1 documentation and diagnose prerequisites.

Exit codes: 0 success, 2 invalid input or configuration, 3 prerequisites missing,
4 generation failure, 5 partial batch failure, 130 cancelled.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import uuid
from pathlib import Path

from . import __version__
from .doctor import diagnose, home

EXIT_OK, EXIT_INPUT, EXIT_PREREQ, EXIT_FAILED, EXIT_PARTIAL, EXIT_CANCELLED = 0, 2, 3, 4, 5, 130
KINDS = ("pbix", "pbip", "tmdl", "bim", "pbir", "extracted", "adf_git", "adf_arm", "adf_resources")


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
    g.add_argument("--source", required=True, help="PBIX file (needs pbi-tools), PBIP project/folder, TMDL folder, model.bim, PBIR folder, pbi-tools extract folder, "
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
    g.add_argument("--pbi-tools", help="PBIX only: path to pbi-tools.exe (otherwise BIDOC_PBI_TOOLS or PATH)")
    g.add_argument("--extract-timeout", type=float, default=900, help="PBIX only: seconds before extraction is stopped")

    b = sub.add_parser("batch", help="document several inputs; each item succeeds or fails on its own",
                       description="Inputs are recognised by their shape: .pbix files, PBIP project folders, "
                                   "TMDL/PBIR folders, model.bim, pbi-tools extracts, ADF Git folders and "
                                   "ARM or resource JSON. Items run one at a time. Exit code 5 means some "
                                   "items failed; retry them with 'bidoc retry ITEM_ID'.")
    b.add_argument("inputs", nargs="+", metavar="INPUT")
    b.add_argument("--output-dir", required=True)
    b.add_argument("--profile", choices=("local", "shared"), default="local")
    b.add_argument("--include-query-code", action="store_true")
    b.add_argument("--environment", default="")
    b.add_argument("--business-area", default="")
    b.add_argument("--owner", default="")
    b.add_argument("--pbi-tools")
    b.add_argument("--extract-timeout", type=float, default=900)
    b.add_argument("--json", action="store_true")

    h = sub.add_parser("history", help="list recent batches and their items")
    h.add_argument("--limit", type=int, default=10)
    h.add_argument("--json", action="store_true")

    r = sub.add_parser("retry", help="run a failed, cancelled or interrupted item again")
    r.add_argument("item_id")
    r.add_argument("--pbi-tools")
    r.add_argument("--json", action="store_true")

    c = sub.add_parser("config", help="show or change settings kept in the generator home (config.json)")
    c.add_argument("--pbi-tools", help="path to pbi-tools.exe; an empty value removes it")
    c.add_argument("--json", action="store_true")

    cn = sub.add_parser("connect", help="connect to a library for direct publishing",
                        description="Checks the library and stores its URL in config.json and the publishing token "
                                    "in Windows Credential Manager (elsewhere: a file only you can read). Create the "
                                    "token in the library under Publishing tokens.")
    cn.add_argument("url", help="library address, e.g. https://docs.example.com")
    cn.add_argument("--token-stdin", action="store_true", help="read the token from standard input instead of a prompt")
    cn.add_argument("--json", action="store_true")

    sub.add_parser("disconnect", help="forget the library connection and remove the stored token")

    pb = sub.add_parser("publish", help="publish generated documents to the connected library",
                        description="Publishes envelope-v1 HTML made by bidoc generate/batch. A new version of an "
                                    "existing document is published against its current ETag; a retry after a lost "
                                    "response returns the original outcome.")
    pb.add_argument("files", nargs="+", metavar="FILE")
    pb.add_argument("--include-query-code", action="store_true",
                    help="ask the library to keep M/SQL from a shared artifact that includes it")
    pb.add_argument("--json", action="store_true")

    w = sub.add_parser("desktop", help="open the desktop app")
    w.add_argument("--pbi-tools")
    w.add_argument("--no-window", action="store_true", help="serve the app on loopback without opening a window")
    w.add_argument("--port", type=int, default=0)

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
    extracted, workspace = None, None
    if args.kind == "pbix":
        if args.engine != "power_bi":
            print("error: a PBIX input needs --engine power_bi", file=sys.stderr)
            return EXIT_INPUT
        report = diagnose(args.pbi_tools)
        if not report["inputs"]["pbix"]["available"]:
            print(f"error: PBIX generation is unavailable: {report['inputs']['pbix']['reason']}. "
                  "PBIP, model and ADF inputs still work.", file=sys.stderr)
            return EXIT_PREREQ
        from .extract import ExtractionCancelled, ExtractionError, check_tool, extract_pbix  # noqa: PLC0415
        from .history import History  # noqa: PLC0415
        history = History(home())
        workspace = str(uuid.uuid4())
        try:
            print("  extracting", file=sys.stderr)
            extracted = extract_pbix(args.source, history.workspace(workspace), check_tool(report["pbi_tools"]),
                                     timeout=args.extract_timeout)
        except ExtractionCancelled:
            return EXIT_CANCELLED
        except ExtractionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_INPUT if exc.code == "INVALID_INPUT" else EXIT_FAILED
        except KeyboardInterrupt:
            return EXIT_CANCELLED
    request = GenerateRequest(
        engine=args.engine, source_path=args.source, source_kind=args.kind, output_dir=args.output_dir,
        profile=args.profile, query_code="included" if args.include_query_code else "withheld",
        document_id=args.document_id, identity_choice=args.identity, title=args.title,
        description=args.description, tags=tuple(args.tag), environment=args.environment,
        business_area=args.business_area, owner=args.owner,
        extracted_path=str(extracted) if extracted else None)
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
    finally:
        if workspace:
            history.release_workspace(workspace)
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


def _runner(pbi_tools):
    from .batch import Runner  # noqa: PLC0415
    from .history import History  # noqa: PLC0415
    report = diagnose(pbi_tools)
    pbix = report["inputs"]["pbix"]
    return Runner(History(home()), pbi_tools=report["pbi_tools"],
                  pbix_ready=None if pbix["available"] else pbix["reason"])


def _print_items(items, as_json):
    if as_json:
        return
    for it in items:
        where = it["artifact_path"] or "; ".join(e["message"] for e in it["errors"])
        print(f"  {it['state']:11} {it['label']}  [{it['item_id']}]" + (f"\n              {where}" if where else ""))


def _outcome(items) -> int:
    ok = sum(it["state"] in ("completed", "local_only") for it in items)
    if any(it["state"] == "cancelled" for it in items):
        return EXIT_CANCELLED
    if ok == len(items):
        return EXIT_OK
    return EXIT_PARTIAL if ok else EXIT_FAILED


def _batch(args) -> int:
    from .batch import Options  # noqa: PLC0415
    runner = _runner(args.pbi_tools)
    opts = Options(output_dir=str(Path(args.output_dir).resolve()), profile=args.profile,
                   query_code="included" if args.include_query_code else "withheld",
                   environment=args.environment, business_area=args.business_area, owner=args.owner,
                   extract_timeout=args.extract_timeout)
    batch_id = runner.submit([str(Path(p).resolve()) for p in args.inputs], opts)
    try:
        runner.wait()
    except KeyboardInterrupt:
        runner.cancel_batch(batch_id)
        runner.wait(30)
    runner.shutdown()
    batch = runner.history.batch(batch_id)
    if args.json:
        print(json.dumps(batch, indent=1))
    else:
        print(f"Batch {batch_id}")
    _print_items(batch["items"], args.json)
    return _outcome(batch["items"])


def _history(args) -> int:
    from .history import History  # noqa: PLC0415
    batches = History(home()).batches(args.limit)
    if args.json:
        print(json.dumps(batches, indent=1))
        return EXIT_OK
    for b in batches:
        print(f"{b['created_at']}  batch {b['batch_id']}  -> {b['output_dir']}")
        _print_items(b["items"], False)
    return EXIT_OK


def _retry(args) -> int:
    runner = _runner(args.pbi_tools)
    try:
        item = runner.retry(args.item_id)
    except KeyError:
        print(f"error: no item {args.item_id}", file=sys.stderr)
        return EXIT_INPUT
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    try:
        runner.wait()
    except KeyboardInterrupt:
        runner.cancel(item["item_id"])
        runner.wait(30)
    runner.shutdown()
    item = runner.history.item(item["item_id"])
    if args.json:
        print(json.dumps(item, indent=1))
    _print_items([item], args.json)
    return _outcome([item])


def _config(args) -> int:
    from .doctor import config  # noqa: PLC0415
    settings = config()
    if args.pbi_tools is not None:
        if args.pbi_tools and not Path(args.pbi_tools).is_file():
            print(f"error: no file at {args.pbi_tools}", file=sys.stderr)
            return EXIT_INPUT
        if args.pbi_tools:
            settings["pbi_tools"] = str(Path(args.pbi_tools).resolve())
        else:
            settings.pop("pbi_tools", None)
        home().mkdir(parents=True, exist_ok=True)
        (home() / "config.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
    print(json.dumps(settings, indent=1) if args.json else
          "\n".join(f"{k}: {v}" for k, v in settings.items()) or "(no settings)")
    return EXIT_OK


def _connected():
    from .credentials import load_token  # noqa: PLC0415
    from .doctor import config  # noqa: PLC0415
    url = config().get("library_url")
    token = load_token(url) if url else None
    return url, token


def _connect(args) -> int:
    import getpass  # noqa: PLC0415

    from .credentials import STORE, save_token  # noqa: PLC0415
    from .doctor import config  # noqa: PLC0415
    from .publisher import LibraryClient, PublishError, normalize_url  # noqa: PLC0415
    try:
        url = normalize_url(args.url)
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    token = (sys.stdin.readline() if args.token_stdin else getpass.getpass("Publishing token: ")).strip()
    if not token:
        print("error: no token given", file=sys.stderr)
        return EXIT_INPUT
    try:
        caps = LibraryClient(url, token).capabilities()
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PREREQ if exc.code == "CREDENTIAL_REJECTED" else EXIT_FAILED
    save_token(url, token)
    settings = config()
    settings["library_url"] = url
    home().mkdir(parents=True, exist_ok=True)
    (home() / "config.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
    out = {"library_url": url, "subject": caps["subject"], "library_version": caps["version"], "token_store": STORE}
    print(json.dumps(out, indent=1) if args.json else
          f"Connected to {url} as {caps['subject']}. The token is kept in {STORE}.")
    return EXIT_OK


def _disconnect(args) -> int:
    from .credentials import delete_token  # noqa: PLC0415
    from .doctor import config  # noqa: PLC0415
    settings = config()
    url = settings.pop("library_url", None)
    if url:
        delete_token(url)
        (home() / "config.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
    print(f"Disconnected from {url}." if url else "Not connected.")
    return EXIT_OK


def _publish(args) -> int:
    from .publisher import LibraryClient, PublishError  # noqa: PLC0415
    url, token = _connected()
    if not url or not token:
        print("error: not connected to a library; run 'bidoc connect URL'", file=sys.stderr)
        return EXIT_PREREQ
    client = LibraryClient(url, token)
    results, failures = [], 0
    for f in args.files:
        path = Path(f)
        try:
            r = client.publish(path.read_bytes(), filename=path.name,
                               query_code="included" if args.include_query_code else "withheld")
            results.append({"file": str(path), **r.__dict__})
            if not args.json:
                print(f"  {r.status:9} {r.title}  ({'new document' if r.new_document else 'new version'}; "
                      f"document {r.document_id}, revision {r.revision_id})")
        except OSError as exc:
            failures += 1
            results.append({"file": str(path), "status": "failed", "error": "IO_ERROR", "message": str(exc)})
            print(f"error: {path}: {exc}", file=sys.stderr)
        except PublishError as exc:
            failures += 1
            results.append({"file": str(path), "status": "failed", "error": exc.code, "message": str(exc)})
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            if exc.code in ("CREDENTIAL_REJECTED", "LIBRARY_UNREACHABLE", "REDIRECT_REFUSED"):
                if args.json:
                    print(json.dumps(results, indent=1))
                return EXIT_PREREQ if exc.code == "CREDENTIAL_REJECTED" else EXIT_FAILED
    if args.json:
        print(json.dumps(results, indent=1))
    if not failures:
        return EXIT_OK
    return EXIT_PARTIAL if failures < len(args.files) else EXIT_FAILED


def _desktop(args) -> int:
    from .desktop.app import run  # noqa: PLC0415
    return run(pbi_tools=args.pbi_tools, window=not args.no_window, port=args.port)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    return {"doctor": _doctor, "generate": _generate, "batch": _batch, "history": _history,
            "retry": _retry, "config": _config, "connect": _connect, "disconnect": _disconnect,
            "publish": _publish, "desktop": _desktop}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
