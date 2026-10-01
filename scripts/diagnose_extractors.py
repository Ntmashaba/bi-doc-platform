"""Why does pbi-tools fail on a PBIX that another extractor reads? A local check with a best-effort redacted report.

    python scripts/diagnose_extractors.py SAMPLE.pbix --pbi-tools C:\\path\\to\\pbi-tools.exe --report diagnose.json

The PBIX never leaves the machine. Redaction is best effort and free-text names are NOT guaranteed to be removed:
paths, URLs, e-mail addresses, GUIDs and connection-string values are replaced in every captured line, and the file's own
name, its folders, the user name and any `--redact-also NAME` (repeatable; use it for server, database or report names)
are replaced wherever they appear. Inspect the report before sharing it.

What it runs, each in its own fresh temporary folder:
  platform    pbi-tools exactly as the platform starts it (stdin closed, own process group, same arguments)
  standalone  pbi-tools as the old standalone tool started it (stdin inherited, same process group)
  short-path  the platform's invocation on a copy of the PBIX in a shorter folder (the report gives both path lengths);
              named renamed-copy when no shorter folder was available, which then tests only the copy
  pbixray     the portable reader, when it is installed
Comparing the first three says whether the way the platform starts the process matters; comparing with pbixray says
whether the file itself reads. Exit 0 = every run succeeded, 1 = at least one failed, 2 = could not run.
"""
import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("components/power-bi", "components/adf", "packages/contracts", "packages/engines", "packages/relationships",
            "apps/generator"):
    sys.path.insert(0, str(ROOT / sub))

from bidoc_generator.diagnostics import cause_lines, exit_code_info, redact  # noqa: E402
from pbidocgen.pbi_tools_runtime import stop_process_tree  # noqa: E402   # the project's bounded process-tree cleanup


def _size_bucket(n: int) -> str:
    for limit, label in ((1 << 20, "<1 MiB"), (10 << 20, "1-10 MiB"), (100 << 20, "10-100 MiB"), (1 << 30, "100 MiB-1 GiB")):
        if n < limit:
            return label
    return ">1 GiB"


def pbix_shape(path: Path) -> dict:
    """Structure only: size bucket and which standard parts exist. No content, no names."""
    out = {"size": _size_bucket(path.stat().st_size)}
    try:
        with zipfile.ZipFile(path) as z:
            names = {n.split("/")[0] for n in z.namelist()}
        out.update(is_zip=True, entries=len(names),
                   parts={p: p in names for p in ("DataModel", "Report", "DataMashup", "Connections", "SecurityBindings")})
    except zipfile.BadZipFile:
        out["is_zip"] = False
    return out


def _run(argv, *, inherit_stdin, new_group, timeout, log):
    """Start one extraction. Timeout and interruption end the whole process tree (bounded), not only the parent.

    The tree is only reachable by group when the child leads its own (new_group). For the standalone variant on Windows
    taskkill /T follows parent-child links; on POSIX (test use only, pbi-tools is Windows-only) descendants of a
    non-leader are not reachable, so that variant is not used for hanging tools there."""
    kwargs = dict(stdout=log, stderr=subprocess.STDOUT, shell=False)
    if not inherit_stdin:
        kwargs["stdin"] = subprocess.DEVNULL
    if new_group:
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
    started = time.monotonic()
    try:
        proc = subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        return {"phase": "launch", "error": exc.strerror or type(exc).__name__}
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_process_tree(proc)
        return {"phase": "timeout", "seconds": timeout}
    except BaseException:                      # Ctrl+C or termination: clean up, then propagate
        stop_process_tree(proc)
        raise
    return {"phase": "extraction", "exit": exit_code_info(code), "seconds": round(time.monotonic() - started, 1)}


def run_variant(name, argv_for, pbix, names, timeout, **how):
    with tempfile.TemporaryDirectory(prefix="bidoc-diag-") as tmp:
        work = Path(tmp)
        target = work / "extracted"
        log_path = work / "out.log"
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            result = _run(argv_for(pbix, target), timeout=timeout, log=log, **how)
        text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        result["ok"] = result.get("phase") == "extraction" and result["exit"]["unsigned"] == 0 and target.is_dir() \
            and any(target.iterdir())
        result["output_lines"] = len(text.splitlines())
        result["first_problems"] = [redact(c, names)[:240] for c in cause_lines(text, 5)]
        result["last_lines"] = [redact(ln, names)[:240] for ln in text.splitlines()[-8:]]
        return {"name": name, **result}


def short_path_variant(build, pbix, names, timeout):
    """The platform's invocation on a copy in the shortest writable folder; renamed-copy if that is not shorter."""
    candidates = ([Path(os.environ.get("SystemDrive", "C:") + "\\")] if os.name == "nt" else []) + [Path(tempfile.gettempdir())]
    for base in candidates:
        try:
            folder = Path(tempfile.mkdtemp(prefix="d", dir=base))
        except OSError:
            continue
        try:
            copy = folder / "a.pbix"
            shutil.copyfile(pbix, copy)
            shorter = len(str(copy)) < len(str(pbix))
            run = run_variant("short-path" if shorter else "renamed-copy", build, copy, names, timeout,
                              inherit_stdin=False, new_group=True)
            run["path_chars"] = {"original": len(str(pbix)), "copy": len(str(copy))}
            return run
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    return {"name": "short-path", "phase": "not run", "reason": "no writable folder for a copy", "ok": None}


def tool_version(tool: str, names) -> str:
    """`tool --version`, bounded and cleaned up like the extractions."""
    try:
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out:
            kwargs = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, shell=False)
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen([tool, "--version"], **kwargs)
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                stop_process_tree(proc)
                return "did not answer within 60 s"
            except BaseException:
                stop_process_tree(proc)
                raise
            out.seek(0)
            lines = out.read().strip().splitlines()
            return redact(lines[0], names)[:120] if lines else ""
    except OSError as exc:
        return f"could not run: {exc.strerror or type(exc).__name__}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pbix", type=Path)
    ap.add_argument("--pbi-tools", required=True, help="path to pbi-tools.exe (Desktop edition)")
    ap.add_argument("--report", type=Path, default=Path("diagnose-report.json"))
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--redact-also", action="append", default=[], metavar="NAME")
    ap.add_argument("--launcher", help=argparse.SUPPRESS)       # tests: run this Python script instead of pbi-tools
    args = ap.parse_args(argv)
    pbix = args.pbix.resolve()
    if not pbix.is_file():
        print("error: the PBIX file was not found", file=sys.stderr)
        return 2
    names = [pbix.stem, pbix.name, *pbix.parts[1:], getpass.getuser(), platform.node(), *args.redact_also]

    def pbi(p, target):
        prefix = [sys.executable, args.launcher] if args.launcher else [args.pbi_tools]
        return prefix + ["extract", str(p), "-extractFolder", str(target), "-modelSerialization", "Raw"]

    runs = [run_variant("platform", pbi, pbix, names, args.timeout, inherit_stdin=False, new_group=True),
            run_variant("standalone", pbi, pbix, names, args.timeout, inherit_stdin=True, new_group=False)]
    runs.append(short_path_variant(pbi, pbix, names, args.timeout))
    try:
        from pbidocgen.portable import status
        state = status()
    except ImportError:
        state = {"ok": False, "reason": "portable reader not installed"}
    if state["ok"]:
        runs.append(run_variant("pbixray", lambda p, t: [sys.executable, "-m", "pbidocgen.portable", str(p), str(t)],
                                pbix, names, args.timeout, inherit_stdin=False, new_group=True))
    else:
        runs.append({"name": "pbixray", "phase": "not run", "reason": redact(state.get("reason", ""), names), "ok": None})
    report = {"purpose": "extractor comparison; best-effort redaction, inspect before sharing", "python": platform.python_version(),
              "os": platform.platform(), "pbi_tools_version": "test launcher" if args.launcher else tool_version(args.pbi_tools, names),
              "pbix": pbix_shape(pbix), "runs": runs}
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write the report: {exc.strerror or exc}", file=sys.stderr)
        return 2
    for r in runs:
        print(f"{r['name']:11} {'ok' if r.get('ok') else r.get('phase', 'failed')}"
              + (f" exit {r['exit']['signed']} ({r['exit']['hex']})" if r.get("exit") else ""))
    print(f"report: {args.report}")
    return 0 if all(r.get("ok") in (True, None) for r in runs) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted; the extractor processes were stopped", file=sys.stderr)
        sys.exit(130)
