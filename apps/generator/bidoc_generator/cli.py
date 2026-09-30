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
from .backend import for_runner, select_backend
from .doctor import diagnose, home

EXIT_OK, EXIT_INPUT, EXIT_PREREQ, EXIT_FAILED, EXIT_PARTIAL, EXIT_CANCELLED = 0, 2, 3, 4, 5, 130
KINDS = ("abf", "pbix", "pbip", "tmdl", "bim", "pbir", "extracted", "adf_git", "adf_arm", "adf_resources")


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
    g.add_argument("--source", required=True, help="PBIX or Tabular ABF file, PBIP project/folder, TMDL folder, model.bim, PBIR folder, pbi-tools extract folder, "
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
    g.add_argument("--model", help="Explicitly pair a thin report with an ABF, BIM or TMDL model; does not verify server identity")
    g.add_argument("--extract-timeout", type=float, default=900, help="PBIX only: seconds before extraction is stopped")
    g.add_argument("--pbixray", action="store_true", help="Alias for --backend pbixray")
    g.add_argument("--backend", choices=("auto", "pbixray", "pbi-tools"), default="auto",
                   help="PBIX extractor. auto (default): a pbi-tools that can run here, else the portable pbixray "
                        "reader, with a note when it falls back. pbi-tools: require it (an error if unusable, never "
                        "a silent switch). pbixray: the portable reader on any OS. Coverage and limits of the "
                        "portable reader: docs/portable-extraction.md")

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

    ex = sub.add_parser("export-library", help="write a portable offline library (opens from file://)",
                        description="Re-renders the given documents with the trusted engines and writes a folder with "
                                    "index.html (catalogue, search, viewer and related-object links). It works with "
                                    "the network off and never changes by itself; run it again to refresh it.")
    ex.add_argument("output_dir")
    ex.add_argument("inputs", nargs="+", metavar="INPUT", help="generated .html documents, or folders of them")
    ex.add_argument("--only", action="append", metavar="DOCUMENT_ID",
                    help="export only these documents; links to the others show as Not included")
    ex.add_argument("--title", default="BI documentation")
    ex.add_argument("--json", action="store_true")

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

    wk = sub.add_parser("worker", help="process hosted jobs for a library (optional R3)",
                        description="Worker mode: connect with a worker token from a library administrator, then "
                                    "run to heartbeat, claim and process jobs one at a time.")
    wsub = wk.add_subparsers(dest="worker_command", required=True)
    wc = wsub.add_parser("connect", help="store the library address and worker token")
    wc.add_argument("url")
    wc.add_argument("--token-stdin", action="store_true")
    wr = wsub.add_parser("run", help="heartbeat, claim and process jobs until stopped")
    wr.add_argument("--once", action="store_true", help="process at most one job, then exit")
    wr.add_argument("--pbi-tools", help="pbi-tools executable for PBIX jobs (otherwise the configured one)")
    wr.add_argument("--extract-timeout", type=float, default=900)
    wsub.add_parser("disconnect", help="forget the worker connection and token")

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
    if args.pbixray:
        if args.backend == "pbi-tools":
            print("error: --pbixray and --pbi-tools are alternatives; use one", file=sys.stderr)
            return EXIT_INPUT
        args.backend = "pbixray"
    extracted, workspace = None, None
    if args.kind in ("pbix", "abf"):
        if args.engine != "power_bi":
            print("error: a PBIX input needs --engine power_bi", file=sys.stderr)
            return EXIT_INPUT
        from .extract import ExtractionCancelled, ExtractionError, check_tool, extract_pbix  # noqa: PLC0415
        from .history import History  # noqa: PLC0415
        selection = select_backend(args.backend, args.pbi_tools, kind=args.kind)
        if selection.note:
            print(f"note: {selection.note}", file=sys.stderr)
        if not selection.available:
            if selection.error_code == "INVALID_INPUT":
                print(f"error: {selection.reason}", file=sys.stderr)
            else:
                lead = "" if selection.explicit else f"{args.kind.upper()} generation is unavailable: "
                print(f"error: {lead}{selection.reason}. PBIP, model and ADF inputs still work.", file=sys.stderr)
            return EXIT_INPUT if selection.error_code == "INVALID_INPUT" else EXIT_PREREQ
        portable = selection.backend == "pbixray"
        history = History(home())
        workspace = str(uuid.uuid4())
        try:
            print("  extracting (portable pbixray)" if portable else "  extracting (pbi-tools)", file=sys.stderr)
            extracted = extract_pbix(args.source, history.workspace(workspace), check_tool(selection.tool),
                                     timeout=args.extract_timeout)
        except ExtractionCancelled:
            return EXIT_CANCELLED
        except ExtractionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_INPUT if exc.code == "INVALID_INPUT" else EXIT_FAILED
        except KeyboardInterrupt:
            return EXIT_CANCELLED
    paired_work = None
    paired = args.model
    if paired and Path(paired).suffix.lower() in (".abf", ".pbix"):
        import tempfile  # noqa: PLC0415
        from .extract import ExtractionError, check_tool, extract_pbix  # noqa: PLC0415
        # Keep the paired extract until generate returns.
        paired_work = tempfile.TemporaryDirectory(prefix="bidoc-paired-")
        try:
            paired = str(extract_pbix(paired, paired_work.name, check_tool("pbixray"), timeout=args.extract_timeout)
                         / "Model" / "database.json")
        except ExtractionError as exc:
            paired_work.cleanup()
            if workspace:
                history.release_workspace(workspace)
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_PREREQ if exc.code == "PREREQUISITE_MISSING" else EXIT_FAILED
    request = GenerateRequest(
        engine=args.engine, source_path=args.source, source_kind=args.kind, output_dir=args.output_dir,
        profile=args.profile, query_code="included" if args.include_query_code else "withheld",
        document_id=args.document_id, identity_choice=args.identity, title=args.title,
        description=args.description, tags=tuple(args.tag), environment=args.environment,
        business_area=args.business_area, owner=args.owner,
        extracted_path=str(extracted) if extracted else None, model_path=paired)
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
        if paired_work:
            paired_work.cleanup()
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
    tool, pbix_ready, selection = for_runner(pbi_tools)
    return Runner(History(home()), pbi_tools=tool, pbix_ready=pbix_ready, backend=selection)


def _announce_backend(runner, items) -> None:
    """Say once, on stderr, which extractor PBIX items use when that is a fallback (never once per file)."""
    if runner.backend is not None and runner.backend.note and any(i.get("kind") == "pbix" for i in items):
        print(f"note: {runner.backend.note}", file=sys.stderr)


def _backend_line(options) -> str | None:
    """One summary line for a batch's PBIX extractor, or None when the batch has no PBIX items."""
    info = (options or {}).get("pbix_backend")
    if not info:
        return None
    if not info.get("available"):
        return f"PBIX extractor: unavailable ({info.get('reason')})"
    return f"PBIX extractor: {info['backend']}" + (f" ({info['fallback']})" if info.get("fallback") else "")


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
    _announce_backend(runner, runner.history.batch(batch_id)["items"])
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
        line = _backend_line(batch["options"])
        if line:
            print(line)
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
        _announce_backend(runner, [item])
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


def _export(args) -> int:
    from .export import ExportError, export_library  # noqa: PLC0415
    try:
        meta = export_library(args.inputs, args.output_dir, only=args.only, title=args.title)
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    if args.json:
        print(json.dumps(meta, indent=1))
    else:
        print(f"Wrote {Path(args.output_dir) / 'index.html'}: {len(meta['documents'])} document(s), "
              f"{meta['relationships']} link(s).")
        for w in meta["warnings"]:
            print(f"  warning: {w}", file=sys.stderr)
    return EXIT_OK


def _worker(args) -> int:
    import getpass  # noqa: PLC0415

    from .credentials import STORE, delete_token, load_token, save_token  # noqa: PLC0415
    from .doctor import config  # noqa: PLC0415
    from .publisher import PublishError, normalize_url  # noqa: PLC0415
    from .worker import Worker, WorkerClient  # noqa: PLC0415
    settings = config()
    if args.worker_command == "disconnect":
        url = settings.pop("worker_library_url", None)
        if url:
            delete_token(url, "worker")
            (home() / "config.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
        print(f"Worker disconnected from {url}." if url else "Worker not connected.")
        return EXIT_OK
    if args.worker_command == "connect":
        try:
            url = normalize_url(args.url)
        except PublishError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_INPUT
        token = (sys.stdin.readline() if args.token_stdin else getpass.getpass("Worker token: ")).strip()
        if not token:
            print("error: no token given", file=sys.stderr)
            return EXIT_INPUT
        worker = Worker(WorkerClient(url, token))
        try:
            worker.heartbeat()
        except PublishError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_PREREQ if exc.code == "CREDENTIAL_REJECTED" else EXIT_FAILED
        save_token(url, token, "worker")
        settings["worker_library_url"] = url
        home().mkdir(parents=True, exist_ok=True)
        (home() / "config.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
        print(f"Worker connected to {url}. The token is kept in {STORE}.")
        return EXIT_OK
    url = settings.get("worker_library_url")
    token = load_token(url, "worker") if url else None
    if not url or not token:
        print("error: the worker is not connected; run 'bidoc worker connect URL'", file=sys.stderr)
        return EXIT_PREREQ
    # Only an explicit --pbi-tools is passed on: the saved setting is found (as an implicit one) by the shared policy.
    worker = Worker(WorkerClient(url, token), pbi_tools=args.pbi_tools,
                    extract_timeout=args.extract_timeout, workspace_root=home() / "worker",
                    log=lambda m: print(m, flush=True))
    try:
        done = worker.run(once=args.once)
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_PREREQ if exc.code == "CREDENTIAL_REJECTED" else EXIT_FAILED
    except KeyboardInterrupt:
        worker.stop.set()
        return EXIT_OK
    print(f"Processed {done} job(s).")
    return EXIT_OK


def _desktop(args) -> int:
    from .desktop.app import run  # noqa: PLC0415
    return run(pbi_tools=args.pbi_tools, window=not args.no_window, port=args.port)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    return {"doctor": _doctor, "generate": _generate, "batch": _batch, "history": _history,
            "retry": _retry, "config": _config, "connect": _connect, "disconnect": _disconnect,
            "publish": _publish, "export-library": _export, "desktop": _desktop,
            "worker": _worker}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
