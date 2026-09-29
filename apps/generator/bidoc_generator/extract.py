"""PBIX extraction with pbi-tools in a child process (handoff section 9).

The tool path is fixed local configuration. Arguments are an array (never a shell).
Each item gets its own workspace. A timeout or cancellation kills the whole process
tree: a POSIX process group, or on Windows a new process group ended with
`taskkill /T /F`. Extraction never refreshes source data.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

POLL_SECONDS = 0.2


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ExtractionCancelled(Exception):
    pass


def check_tool(tool: str | None) -> str:
    """The configured pbi-tools Desktop executable, or ExtractionError."""
    if not tool:
        raise ExtractionError("PREREQUISITE_MISSING", "pbi-tools is not configured; run 'bidoc doctor'")
    path = Path(tool)
    if not path.is_file():
        raise ExtractionError("PREREQUISITE_MISSING", f"pbi-tools was not found at {tool}")
    if path.suffix.lower() in {".bat", ".cmd", ".ps1", ".sh"}:
        raise ExtractionError("PREREQUISITE_MISSING", "configure the pbi-tools executable, not a script wrapper")
    if "pbi-tools.core" in path.name.lower():
        raise ExtractionError("PREREQUISITE_MISSING", "PBIX extraction needs pbi-tools Desktop, not pbi-tools.core")
    return str(path.resolve())


def _spawn(command, log):
    if os.name == "nt":
        return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                shell=False, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            shell=False, start_new_session=True)


def kill_tree(proc: subprocess.Popen) -> None:
    """End the process and everything it started."""
    if proc.poll() is not None and os.name == "nt":
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, shell=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)     # the child leads its own session and group
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def extract_pbix(source, workspace, tool, *, timeout: float = 900, cancellation=None, command=None) -> Path:
    """Extract `source` into `workspace/<pbix stem>`; returns that folder."""
    source, workspace = Path(source), Path(workspace)
    if not source.is_file() or source.suffix.lower() != ".pbix":
        raise ExtractionError("INVALID_INPUT", f"not a PBIX file: {source.name}")
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / source.stem
    log_path = workspace / "pbi-tools.log"
    argv = list(command or [tool]) + ["extract", str(source), "-extractFolder", str(target),
                                      "-modelSerialization", "Raw"]
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = _spawn(argv, log)
        try:
            while proc.poll() is None:
                if cancellation is not None and cancellation.is_set():
                    kill_tree(proc)
                    raise ExtractionCancelled()
                if time.monotonic() - started > timeout:
                    kill_tree(proc)
                    raise ExtractionError("EXTRACTION_TIMEOUT", f"pbi-tools did not finish within {timeout:g} s")
                time.sleep(POLL_SECONDS)
        except BaseException:
            if proc.poll() is None:
                kill_tree(proc)
            raise
    if proc.returncode:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:].strip()
        raise ExtractionError("EXTRACTION_FAILED", f"pbi-tools exited with code {proc.returncode}. {tail}")
    beside = source.with_suffix("")
    if not (target.is_dir() and any(target.iterdir())) and (beside / "Model").is_dir():
        # Older PBIX files: pbi-tools ignores -extractFolder and writes beside the PBIX.
        raise ExtractionError("EXTRACTION_FAILED", f"pbi-tools wrote its output beside the PBIX ({beside}) "
                                                   "instead of the workspace; remove it and upgrade pbi-tools")
    if not target.is_dir() or not any(target.iterdir()):
        raise ExtractionError("EXTRACTION_FAILED", "pbi-tools finished but wrote no extract")
    return target


def pbixray_command() -> list[str]:
    """Run the pbixray extractor as the extraction command (same child-process handling as pbi-tools).

    An approximation of a pbi-tools extract that works on any OS; see pbidocgen.pbixray_extract."""
    import importlib.util  # noqa: PLC0415
    if importlib.util.find_spec("pbixray") is None:
        raise ExtractionError("PREREQUISITE_MISSING", "pbixray is not installed; run: pip install pbixray")
    if importlib.util.find_spec("pbidocgen.pbixray_extract") is None:
        raise ExtractionError("PREREQUISITE_MISSING", "this pbi-doc-gen does not include the pbixray extractor; upgrade it")
    return [sys.executable, "-m", "pbidocgen.pbixray_extract"]


def python_tool(script: str) -> list[str]:
    """Test helper: run a Python script as the 'tool' (the real tool is an .exe)."""
    return [sys.executable, script]
