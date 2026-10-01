"""Why does pbi-tools fail on a PBIX that another extractor reads? A local check whose report is safe to share.

    python scripts/diagnose_extractors.py SAMPLE.pbix --pbi-tools C:\\path\\to\\pbi-tools.exe --report diagnose.json

The PBIX never leaves the machine. The report holds no file name, folder, report, server or database name and no
connection string: paths, URLs, e-mail addresses, GUIDs and connection-string values are removed from every captured
line, and the file's own name, its folders and the user name are removed wherever they appear. Read the report before
sending it; redaction is best effort. `--redact-also NAME` (repeatable) removes further names such as a server.

What it runs, each in its own fresh temporary folder:
  platform    pbi-tools exactly as the platform starts it (stdin closed, own process group, same arguments)
  standalone  pbi-tools as the old standalone tool started it (stdin inherited, same process group)
  short-path  the platform's invocation on a copy of the PBIX at a short path (rules out path-length problems)
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
        proc.kill()
        return {"phase": "timeout", "seconds": timeout}
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


def tool_version(tool: str, names) -> str:
    try:
        out = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return redact((out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else "", names)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run: {type(exc).__name__}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pbix", type=Path)
    ap.add_argument("--pbi-tools", required=True, help="path to pbi-tools.exe (Desktop edition)")
    ap.add_argument("--report", type=Path, default=Path("diagnose-report.json"))
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--redact-also", action="append", default=[], metavar="NAME")
    args = ap.parse_args(argv)
    pbix = args.pbix.resolve()
    if not pbix.is_file():
        print("error: the PBIX file was not found", file=sys.stderr)
        return 2
    names = [pbix.stem, pbix.name, *pbix.parts[1:], getpass.getuser(), platform.node(), *args.redact_also]

    def pbi(p, target):
        return [args.pbi_tools, "extract", str(p), "-extractFolder", str(target), "-modelSerialization", "Raw"]

    runs = [run_variant("platform", pbi, pbix, names, args.timeout, inherit_stdin=False, new_group=True),
            run_variant("standalone", pbi, pbix, names, args.timeout, inherit_stdin=True, new_group=False)]
    short_dir = Path(tempfile.mkdtemp(prefix="d"))
    try:
        short = short_dir / "a.pbix"
        shutil.copyfile(pbix, short)
        runs.append(run_variant("short-path", pbi, short, names, args.timeout, inherit_stdin=False, new_group=True))
    finally:
        shutil.rmtree(short_dir, ignore_errors=True)
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
    report = {"purpose": "extractor comparison; redacted, no file or folder names", "python": platform.python_version(),
              "os": platform.platform(), "pbi_tools_version": tool_version(args.pbi_tools, names),
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
    sys.exit(main())
