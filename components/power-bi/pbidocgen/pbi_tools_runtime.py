"""Whether a pbi-tools executable can be used on this machine, without extracting anything.

Two callers share this: the standalone command line (`pbidocgen.pbix_batch.resolve_tool`) and the platform's backend
policy (`bidoc_generator.backend`), so an executable that one accepts is accepted by the other. It lives here so the
engine never depends on the platform application.

"Usable" means three things, checked in this order and each reported as a reason string (None = fine):

1. `executable_problem`: the file exists and is not a script wrapper or pbi-tools.core;
2. `prerequisite_problem`: this machine can run pbi-tools Desktop at all (Windows with Power BI Desktop);
3. `launch_problem`: the file can be started and exits within a bounded time.

The launch probe shows the file is a launchable program on this platform. It is not a functional test: a pbi-tools that
starts can still fail on a particular PBIX, and that failure belongs to the extraction, not to readiness.
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

PROBE_TIMEOUT_SECONDS = 15.0
_SCRIPT_SUFFIXES = {".bat", ".cmd", ".ps1", ".sh"}
_launch_cache: dict[tuple, str | None] = {}


def executable_problem(tool) -> str | None:
    """Why `tool` is not a usable pbi-tools Desktop file, judged from the file alone."""
    path = Path(str(tool))
    if not path.is_file():
        return f"pbi-tools was not found at {tool}"
    if path.suffix.lower() in _SCRIPT_SUFFIXES:
        return "configure the pbi-tools executable, not a script wrapper"
    if "pbi-tools.core" in path.name.lower():
        return "PBIX extraction needs pbi-tools Desktop, not pbi-tools.core"
    return None


def power_bi_desktop() -> str | None:
    """The installed Power BI Desktop version (or "Microsoft Store"), or None. Windows only."""
    if platform.system() != "Windows":
        return None
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Microsoft Power BI Desktop") as key:
            return winreg.QueryValueEx(key, "Version")[0]
    except OSError:
        pass
    store = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "PBIDesktopStore.exe"
    return "Microsoft Store" if store.exists() else None


def prerequisite_problem() -> str | None:
    """Why pbi-tools cannot run on this machine whatever executable is configured, or None."""
    if platform.system() != "Windows":
        return "PBIX extraction with pbi-tools runs on Windows only"
    if not power_bi_desktop():
        return "Power BI Desktop is not installed (pbi-tools needs it to read PBIX files)"
    return None


def launch_problem(tool, timeout: float = PROBE_TIMEOUT_SECONDS) -> str | None:
    """Start `tool --version` once, bounded by `timeout`; None when it started and finished.

    Any exit code counts: the aim is to find a file that cannot be started (wrong platform, not an executable,
    blocked, hangs), not to judge its output. The result is cached per file version."""
    path = Path(str(tool))
    try:
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        return f"pbi-tools could not be read at {tool}: {exc}"
    if key in _launch_cache:
        return _launch_cache[key]
    problem = None
    try:
        proc = subprocess.Popen([str(path), "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, shell=False)
    except OSError as exc:
        problem = f"pbi-tools at {tool} could not be started ({exc.strerror or exc})"
    else:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            problem = f"pbi-tools at {tool} did not respond within {timeout:g} s"
    _launch_cache[key] = problem
    return problem


def unusable_reason(tool) -> str | None:
    """The first reason `tool` cannot be used as the PBIX extractor here, or None when it can."""
    return executable_problem(tool) or prerequisite_problem() or launch_problem(tool)
