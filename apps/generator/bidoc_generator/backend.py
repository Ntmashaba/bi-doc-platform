"""Which PBIX extractor to use, and whether it is ready, decided once for every entry point.

`bidoc generate`, `bidoc batch` and its siblings, the desktop app, the worker and `bidoc doctor` all call
`select_backend`, so the same machine and configuration give the same answer everywhere.

Two kinds of input are kept apart on purpose:

* an **explicit** pbi-tools path (`--pbi-tools`), or `--backend pbi-tools` / `--backend pbixray`, is the caller's
  instruction. If it cannot be honoured the result is an error with a fix; it is never replaced by the other backend.
* an **implicit** pbi-tools is one that was only configured (BIDOC_PBI_TOOLS, config.json) or found on PATH. It is used
  when it can run; when it cannot, the portable reader is used instead and the reason is reported.

Readiness is about the backend, not the file: it says the chosen extractor is present and can start. Whether one
particular PBIX extracts is decided when it is extracted, and a failed extraction is reported as such. It is never
retried through the other backend.

The reusable checks (file, platform prerequisites, launch probe) live in `pbidocgen.pbi_tools_runtime`, which the
standalone engine command line uses too.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

BACKENDS = ("auto", "pbixray", "pbi-tools")
PORTABLE = "pbixray"

_INSTALL_HINT = ("pass --pbi-tools EXE, or set BIDOC_PBI_TOOLS or config.json (bidoc config --pbi-tools EXE), "
                 "or put pbi-tools on PATH")


@dataclass(frozen=True)
class Selection:
    """The outcome of backend selection for one PBIX/ABF input kind."""

    backend: str | None            # "pbi-tools", "pbixray", or None when nothing is usable
    tool: str | None               # what extract.check_tool / extract_pbix take: an executable path or "pbixray"
    reason: str | None = None      # when backend is None: why (actionable)
    error_code: str | None = None  # when backend is None: INVALID_INPUT (conflicting options) or PREREQUISITE_MISSING
    note: str | None = None        # set when the portable reader replaced an implicit pbi-tools that cannot run
    explicit: bool = False         # the caller named the backend or the pbi-tools path
    pbi_tools_problem: str | None = field(default=None)   # why the pbi-tools that was looked at is unusable, if so

    @property
    def available(self) -> bool:
        return self.backend is not None

    @property
    def fell_back(self) -> bool:
        return self.note is not None

    def as_dict(self) -> dict:
        return {"backend": self.backend, "tool": self.tool, "available": self.available, "reason": self.reason,
                "fallback": self.note, "explicit": self.explicit, "pbi_tools_problem": self.pbi_tools_problem}


def _runtime():
    from pbidocgen import pbi_tools_runtime  # noqa: PLC0415 - looked up at call time so tests can patch it
    return pbi_tools_runtime


def portable_status() -> dict:
    """pbidocgen.portable.status(), or an explanation when the engine itself is missing."""
    try:
        from pbidocgen.portable import status  # noqa: PLC0415
    except ImportError:
        return {"installed": None, "supported": None, "ok": False, "reason": "pbi-doc-gen is not installed"}
    return status()


def implicit_pbi_tools() -> str | None:
    """A pbi-tools that was only configured or found, never named for this call: BIDOC_PBI_TOOLS, config.json, PATH.

    A configured path is returned even when the file is missing, so that "configured but gone" is reported and falls
    back, instead of being mistaken for "nothing configured". PATH is searched only for a name; the disk is never
    scanned for something to run."""
    from .doctor import config  # noqa: PLC0415
    configured = os.environ.get("BIDOC_PBI_TOOLS") or config().get("pbi_tools")
    if configured:
        return str(configured)
    return shutil.which("pbi-tools") or shutil.which("pbi-tools.exe")


def _fail(reason: str, code: str = "PREREQUISITE_MISSING", *, explicit: bool = False, problem: str | None = None):
    return Selection(None, None, reason=reason, error_code=code, explicit=explicit, pbi_tools_problem=problem)


def _portable(state: dict, *, explicit: bool, note: str | None = None, problem: str | None = None) -> Selection:
    if state["ok"]:
        return Selection(PORTABLE, PORTABLE, note=note, explicit=explicit, pbi_tools_problem=problem)
    return _fail(state["reason"], explicit=explicit, problem=problem)


def select_backend(requested: str = "auto", explicit_tool: str | None = None, *, kind: str = "pbix") -> Selection:
    """Choose the extractor for a `pbix` or `abf` input.

    requested      "auto", "pbixray" or "pbi-tools" (what --backend says; --pbixray is "pbixray").
    explicit_tool  a pbi-tools path the caller named (--pbi-tools). Anything only configured is found here.
    kind           "pbix" or "abf". An ABF file is only ever read by the portable reader.
    """
    if requested not in BACKENDS:
        raise ValueError(f"unknown backend {requested!r}; use one of {', '.join(BACKENDS)}")
    if explicit_tool == PORTABLE:                # the older spelling: diagnose("pbixray")
        requested, explicit_tool = PORTABLE, None
    state = portable_status()

    if kind == "abf":
        if explicit_tool or requested == "pbi-tools":
            return _fail("an ABF file cannot be combined with --pbi-tools; it is read by the portable reader",
                         "INVALID_INPUT", explicit=True)
        return _portable(state, explicit=requested == PORTABLE)

    if requested == PORTABLE:
        if explicit_tool:
            return _fail("--pbixray and --pbi-tools are alternatives; use one", "INVALID_INPUT", explicit=True)
        return _portable(state, explicit=True)

    runtime = _runtime()
    if explicit_tool or requested == "pbi-tools":
        tool = explicit_tool or implicit_pbi_tools()
        if not tool:
            return _fail(f"--backend pbi-tools needs pbi-tools: {_INSTALL_HINT}", explicit=True)
        problem = runtime.unusable_reason(tool)
        if problem:
            return _fail(f"pbi-tools cannot be used: {problem}", explicit=True, problem=problem)
        return Selection("pbi-tools", str(tool), explicit=True)

    tool = implicit_pbi_tools()
    if not tool:
        selection = _portable(state, explicit=False)
        if selection.available:
            return selection
        return _fail(f"pbi-tools is not configured ({_INSTALL_HINT}) and the portable reader is not available: "
                     f"{state['reason']}")
    problem = runtime.unusable_reason(tool)
    if not problem:
        return Selection("pbi-tools", str(tool))
    if state["ok"]:
        return _portable(state, explicit=False, problem=problem,
                         note=f"the configured pbi-tools cannot be used here ({problem}), so the portable pbixray "
                              "reader is used")
    return _fail(f"PBIX extraction is unavailable: pbi-tools cannot be used ({problem}) and the portable reader is "
                 f"not available ({state['reason']})", problem=problem)


def for_runner(explicit_tool: str | None = None, requested: str = "auto") -> tuple[str | None, str | None, Selection]:
    """(tool for Runner/Worker, reason PBIX is unavailable or None, the selection).

    The tool is what `extract.check_tool` accepts; the reason is what a PBIX item reports when nothing is usable. ABF
    readiness is separate: it needs only the portable reader (see doctor.diagnose)."""
    selection = select_backend(requested, explicit_tool)
    return selection.tool, None if selection.available else selection.reason, selection
