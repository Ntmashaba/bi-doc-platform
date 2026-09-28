"""B16: evidence for unattended PBIX extraction on a real Windows host (handoff 10, A16, A17).

Run on the Windows machine that will host the worker, as the account that will run it,
with Power BI Desktop and pbi-tools installed. Every run appends one JSON line to a
results file; `report` turns that file into the verification report. See
docs/b16/CHECKLIST.md for the order of the runs.

    python b16_gate.py env
    python b16_gate.py extract  --scenario interactive --pbix C:\\b16\\Sample.pbix --results C:\\b16\\results.jsonl
    python b16_gate.py schedule --scenario after_reboot --pbix ... --results ... --account CONTOSO\\svc-bidoc
    python b16_gate.py schedule --scenario locked --at 14:05 --pbix ... --results ... --account ...
    python b16_gate.py schedule --scenario logged_off --at 14:20 --pbix ... --results ... --account ...
    python b16_gate.py unschedule
    python b16_gate.py worker   --scenario worker_unattended --results ...   (one job via the connected library)
    python b16_gate.py report   --results C:\\b16\\results.jsonl --out VERIFICATION.md

Nothing here changes the machine except `schedule` (a Task Scheduler task per scenario,
named BIDOC-B16-*) and the working folder given with --work (default: next to results).
The PBIX is only read; extraction never refreshes data. Results hold no report content:
only timings, counts, versions, session facts and error messages.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

SCENARIOS = {
    "interactive": "Signed in at the console, session unlocked (baseline).",
    "after_reboot": "Machine restarted; task runs at startup before anyone signs in.",
    "locked": "Account signed in, session locked (Win+L) when the task runs.",
    "logged_off": "Account signed out; task set to run whether the user is signed in or not.",
    "worker_interactive": "bidoc worker processes one queued library job, signed in.",
    "worker_unattended": "bidoc worker processes one queued job with the session locked or signed out.",
    "worker_recovery": "Worker killed mid-job; a second worker run recovers the lease and publishes once.",
}
TASK_PREFIX = "BIDOC-B16-"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- facts about the session this run is in ------------------------------------------------

def _win_session() -> dict:
    import ctypes
    from ctypes import wintypes
    k32, u32 = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("user32", use_last_error=True)
    sid = wintypes.DWORD()
    k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(sid))
    k32.GetTickCount64.restype = ctypes.c_ulonglong
    station = u32.GetProcessWindowStation()
    buf = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    u32.GetUserObjectInformationW(station, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed))   # UOI_NAME
    u32.OpenInputDesktop.restype = wintypes.HANDLE
    desk = u32.OpenInputDesktop(0, False, 0x0100)                # DESKTOP_SWITCHDESKTOP
    if desk:
        u32.CloseDesktop(desk)
    return {"session_id": sid.value, "window_station": buf.value,
            "interactive_station": buf.value.lower() == "winsta0",
            # No input desktop: the session is locked, or there is no console session at all.
            "input_desktop_available": bool(desk),
            "uptime_minutes": round(k32.GetTickCount64() / 60000, 1)}


def _run(cmd, timeout=60) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()[:4000]
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(unavailable: {exc})"


def environment(pbi_tools: str | None) -> dict:
    env = {"host": platform.node(), "os": platform.platform(), "python": sys.version.split()[0],
           "account": _run(["whoami"]) if os.name == "nt" else os.environ.get("USER", ""),
           "elevated": _run(["whoami", "/groups"]).count("S-1-16-12288") > 0 if os.name == "nt" else False,
           "signed_in_users": _run(["quser"]) if os.name == "nt" else "", "pbi_tools": pbi_tools}
    if os.name == "nt":
        env.update(_win_session())
    try:
        from bidoc_generator import __version__ as gen
        from bidoc_engines import power_bi
        env["generator"] = gen
        env["engine"] = f"{power_bi.ENGINE} {power_bi.ENGINE_VERSION}"
    except ImportError as exc:
        env["generator"] = f"(not importable: {exc})"
    if pbi_tools and Path(pbi_tools).is_file():
        env["pbi_tools_info"] = _run([pbi_tools, "info"], timeout=120)      # lists Power BI Desktop installs
    return env


# ---- the measured work ---------------------------------------------------------------------

def run_extract(pbix: Path, pbi_tools: str | None, work: Path, timeout: float, tool_command=None) -> dict:
    """Extract with pbi-tools, generate a shared document and validate it. No report content kept."""
    from bidoc_contracts import validate_artifact
    from bidoc_engines.generate import GenerateRequest, generate
    from bidoc_generator.extract import check_tool, extract_pbix
    workspace = Path(tempfile.mkdtemp(prefix="b16-", dir=work))
    out = {"pbix": pbix.name, "pbix_bytes": pbix.stat().st_size}
    try:
        t = time.monotonic()
        extracted = extract_pbix(pbix, workspace / "extract", None if tool_command else check_tool(pbi_tools),
                                 timeout=timeout, command=tool_command)
        out["extract_seconds"] = round(time.monotonic() - t, 1)
        t = time.monotonic()
        r = generate(GenerateRequest(engine="power_bi", source_path=str(pbix), source_kind="pbix",
                                     output_dir=str(workspace / "out"), profile="shared",
                                     extracted_path=str(extracted), mapping_dir=str(workspace / "identity")))
        out["generate_seconds"] = round(time.monotonic() - t, 1)
        if r.status != "completed":
            return {**out, "status": "failed", "error": "; ".join(e.get("message", "") for e in r.errors)[:1000]}
        manifest = validate_artifact(Path(r.artifact_path).read_bytes())
        kinds = {}
        for o in manifest["objects"]:
            kinds[o["kind"]] = kinds.get(o["kind"], 0) + 1
        return {**out, "status": "passed", "objects": kinds, "sections": len(manifest["sections"])}
    except Exception as exc:  # noqa: BLE001 - every failure is evidence
        return {**out, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:1000],
                "trace": traceback.format_exc()[-1500:]}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_worker(timeout: float) -> dict:
    """Process at most one queued library job with the connected worker (bidoc worker connect)."""
    from bidoc_generator.credentials import load_token
    from bidoc_generator.doctor import config, home
    from bidoc_generator.worker import Worker, WorkerClient
    settings = config()
    url = settings.get("worker_library_url")
    token = load_token(url, "worker") if url else None
    if not url or not token:
        return {"status": "failed", "error": "the worker is not connected; run 'bidoc worker connect URL' first"}
    lines = []
    w = Worker(WorkerClient(url, token), pbi_tools=settings.get("pbi_tools"), extract_timeout=timeout,
               workspace_root=home() / "worker", log=lines.append)
    t = time.monotonic()
    done = w.run(once=True)
    outcome = next((ln for ln in reversed(lines) if ": succeeded" in ln or " -> " in ln or "abandoned" in ln), "")
    status = "passed" if done and ": succeeded" in outcome else "failed" if done else "no_job"
    return {"status": status, "seconds": round(time.monotonic() - t, 1), "library": url, "log": lines[-10:]}


def record(results: Path, scenario: str, pbi_tools, result: dict) -> dict:
    entry = {"scenario": scenario, "recorded_at": now(), "environment": environment(pbi_tools), "result": result}
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


# ---- Task Scheduler ----------------------------------------------------------------------

def schedule(args) -> int:
    if os.name != "nt":
        print("schedule works on Windows only", file=sys.stderr)
        return 2
    me = Path(__file__).resolve()
    action = (f'"{sys.executable}" "{me}" extract --scenario {args.scenario} --pbix "{args.pbix}" '
              f'--results "{args.results}" --timeout {args.timeout}'
              + (f' --pbi-tools "{args.pbi_tools}"' if args.pbi_tools else ""))
    if args.scenario.startswith("worker"):
        action = f'"{sys.executable}" "{me}" worker --scenario {args.scenario} --results "{args.results}"'
    cmd = ["schtasks", "/Create", "/F", "/TN", TASK_PREFIX + args.scenario, "/TR", action, "/RL", "LIMITED",
           "/RU", args.account]
    if args.scenario == "after_reboot":
        cmd += ["/SC", "ONSTART"]
    else:
        if not args.at:
            print("--at HH:MM is required for this scenario", file=sys.stderr)
            return 2
        cmd += ["/SC", "ONCE", "/ST", args.at]
    if args.scenario in ("after_reboot", "logged_off", "worker_unattended"):
        cmd += ["/RP", "*"]                  # run whether the user is signed in or not: prompts for the password
    else:
        cmd += ["/IT"]                        # only when the user is signed in (interactive token)
    print("Creating:", " ".join(c if " " not in c else f'"{c}"' for c in cmd))
    return subprocess.run(cmd).returncode


def unschedule(_args) -> int:
    for name in SCENARIOS:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_PREFIX + name], capture_output=True)
    print("Removed any BIDOC-B16-* tasks.")
    return 0


# ---- report ------------------------------------------------------------------------------

def report(results: Path) -> str:
    entries = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line.strip()]
    by = {}
    for e in entries:
        by.setdefault(e["scenario"], []).append(e)
    lines = ["# B16 verification: unattended PBIX extraction", "",
             f"Generated {now()} from {len(entries)} recorded run(s). A condition is **supported** when it "
             "passed at least twice and its latest three runs have no failure; otherwise it is not claimed.", "",
             "| Condition | Runs | Passed | Verdict | Latest evidence |", "|---|---|---|---|---|"]
    verdicts = {}
    for name, desc in SCENARIOS.items():
        runs = by.get(name, [])
        passed = sum(1 for r in runs if r["result"].get("status") == "passed")
        recent_fail = any(r["result"].get("status") == "failed" for r in runs[-3:])
        verdict = "supported" if passed >= 2 and not recent_fail else "not verified" if not runs else "not supported"
        verdicts[name] = verdict
        last = runs[-1] if runs else None
        ev = "—"
        if last:
            env, res = last["environment"], last["result"]
            ev = (f"{last['recorded_at']}; {env.get('account', '?')}; session {env.get('session_id', '?')} "
                  f"({env.get('window_station', '?')}, input desktop "
                  f"{'yes' if env.get('input_desktop_available') else 'no'}); uptime {env.get('uptime_minutes', '?')} min; "
                  + (f"extract {res.get('extract_seconds')} s" if res.get("status") == "passed" and "extract_seconds" in res
                     else res.get("error") or res.get("status")))
        lines.append(f"| {name}: {desc} | {len(runs)} | {passed} | **{verdict}** | {ev.replace('|', '/')} |")
    host = entries[-1]["environment"] if entries else {}
    lines += ["", "## Configuration", "",
              f"- Host: {host.get('host', '?')} ({host.get('os', '?')})",
              f"- Account: {host.get('account', '?')} (elevated: {host.get('elevated', '?')})",
              f"- Generator {host.get('generator', '?')}, engine {host.get('engine', '?')}, pbi-tools at {host.get('pbi_tools')}",
              "", "## Conclusion", ""]
    unattended = [n for n in ("after_reboot", "locked", "logged_off", "worker_unattended") if verdicts[n] == "supported"]
    if verdicts["interactive"] != "supported":
        lines.append("Extraction does not yet work reliably even when signed in; R3 hosted processing is not released.")
    elif not unattended:
        lines.append("Only signed-in, unlocked extraction is verified. Hosted processing may be offered only while "
                     "that account stays signed in and unlocked; otherwise the local generator is the supported route.")
    else:
        lines.append("Verified unattended conditions: " + ", ".join(unattended) + ". R3 may be released for exactly "
                     "these conditions on this configuration; anything not listed stays unsupported.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("env")
    e.add_argument("--pbi-tools")
    for name in ("extract", "schedule"):
        x = sub.add_parser(name)
        x.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
        x.add_argument("--pbix", type=Path)
        x.add_argument("--results", type=Path, required=True)
        x.add_argument("--pbi-tools")
        x.add_argument("--timeout", type=float, default=900)
        x.add_argument("--work", type=Path)
        x.add_argument("--tool-command", nargs="+", help=argparse.SUPPRESS)      # tests: a stand-in extractor
        if name == "schedule":
            x.add_argument("--account", required=True, help=r"DOMAIN\user the worker will run as")
            x.add_argument("--at", help="HH:MM (24 h) for locked / logged_off / worker scenarios")
    wk = sub.add_parser("worker")
    wk.add_argument("--scenario", required=True, choices=[s for s in SCENARIOS if s.startswith("worker")])
    wk.add_argument("--results", type=Path, required=True)
    wk.add_argument("--timeout", type=float, default=900)
    sub.add_parser("unschedule")
    r = sub.add_parser("report")
    r.add_argument("--results", type=Path, required=True)
    r.add_argument("--out", type=Path)
    args = p.parse_args(argv)

    if args.cmd == "env":
        print(json.dumps(environment(args.pbi_tools), indent=1))
        return 0
    if args.cmd == "unschedule":
        return unschedule(args)
    if args.cmd == "report":
        text = report(args.results)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        print(text)
        return 0
    if args.cmd == "schedule":
        if not args.scenario.startswith("worker") and not args.pbix:
            p.error("--pbix is required")
        return schedule(args)
    if args.cmd == "worker":
        entry = record(args.results, args.scenario, None, run_worker(args.timeout))
    else:
        if not args.pbix or not args.pbix.is_file():
            p.error("--pbix must name an existing .pbix file")
        pbi_tools = args.pbi_tools or os.environ.get("BIDOC_PBI_TOOLS") or _configured_tool()
        work = args.work or args.results.parent
        work.mkdir(parents=True, exist_ok=True)
        entry = record(args.results, args.scenario, pbi_tools,
                       run_extract(args.pbix, pbi_tools, work, args.timeout, args.tool_command))
    res = entry["result"]
    print(f"{args.scenario}: {res['status']}" + (f" ({res.get('error')})" if res.get("error") else ""))
    return 0 if res["status"] == "passed" else 1


def _configured_tool():
    try:
        from bidoc_generator.doctor import config
        return config().get("pbi_tools")
    except ImportError:
        return None


if __name__ == "__main__":
    sys.exit(main())
