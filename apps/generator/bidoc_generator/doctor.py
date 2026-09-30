"""Prerequisite diagnostics: what this machine can generate, and how to fix what it cannot."""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path


def config() -> dict:
    """Settings kept in the generator home (config.json), so upgrades keep them."""
    try:
        data = json.loads((home() / "config.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _power_bi_desktop():
    from pbidocgen.pbi_tools_runtime import power_bi_desktop  # noqa: PLC0415 - one implementation, shared with the engine
    return power_bi_desktop()


def webview2_version():
    """Microsoft Edge WebView2 runtime (the desktop window's browser engine), or None."""
    if platform.system() != "Windows":
        return None
    import winreg  # noqa: PLC0415
    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, key in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + client),
                      (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + client),
                      (winreg.HKEY_CURRENT_USER, "SOFTWARE\\" + client)):
        try:
            with winreg.OpenKey(root, key) as k:
                value = winreg.QueryValueEx(k, "pv")[0]
                if value and value != "0.0.0.0":
                    return value
        except OSError:
            continue
    return None


def diagnose(pbi_tools: str | None = None, requested: str = "auto") -> dict:
    """What this machine can generate. `pbi_tools` is an explicit pbi-tools path (or the older "pbixray" spelling of
    the portable reader); `requested` is --backend. PBIX readiness comes from backend.select_backend, the same policy
    generate, batch, the desktop app and the worker use."""
    checks, ready = [], {}
    py_ok = sys.version_info >= (3, 11)
    bundled = " (bundled with the installed app)" if getattr(sys, "frozen", False) else ""
    checks.append({"check": "python", "ok": py_ok, "detail": platform.python_version() + bundled,
                   "fix": None if py_ok else "Install Python 3.11 or later."})
    engines = {}
    for engine, module in (("pbi-doc-gen", "pbidocgen"), ("adf-doc-gen", "adfdocgen")):
        try:
            engines[engine] = __import__(module).__version__
            checks.append({"check": engine, "ok": True, "detail": engines[engine], "fix": None})
        except (ImportError, AttributeError):
            checks.append({"check": engine, "ok": False, "detail": "not installed",
                           "fix": "Install the platform packages: pip install -r requirements.txt"})
    pbi_ok, adf_ok = "pbi-doc-gen" in engines and py_ok, "adf-doc-gen" in engines and py_ok
    for kind in ("pbip", "tmdl", "bim", "pbir", "extracted"):
        ready[kind] = {"available": pbi_ok, "reason": None if pbi_ok else "pbi-doc-gen is not installed"}
    for kind in ("adf_git", "adf_arm", "adf_resources"):
        ready[kind] = {"available": adf_ok, "reason": None if adf_ok else "adf-doc-gen is not installed"}

    windows = platform.system() == "Windows"
    if pbi_ok:
        from . import backend  # noqa: PLC0415 - backend reads config() from this module, so it is imported lazily
        from pbidocgen import pbi_tools_runtime as runtime  # noqa: PLC0415
        selection = backend.select_backend(requested, pbi_tools)
        pbixray_state = backend.portable_status()
        named = None if pbi_tools in (None, backend.PORTABLE) else pbi_tools       # an explicit pbi-tools path
        seen = named or backend.implicit_pbi_tools()                                # what a run would look at
        problem = runtime.unusable_reason(seen) if seen else None
    else:
        selection = None
        pbixray_state = {"installed": None, "ok": False, "reason": "pbi-doc-gen is not installed"}
        seen, problem = None, None
    checks.append({"check": "pbixray", "ok": pbixray_state["ok"],
                   "detail": pbixray_state["installed"] or "not installed",
                   "fix": None if pbixray_state["ok"] else pbixray_state["reason"]})
    checks.append({"check": "pbi-tools", "ok": bool(seen) and not problem, "detail": seen or "not found",
                   "fix": None if seen and not problem else
                   (problem or "Install pbi-tools Desktop (https://pbi.tools, AGPL-3.0) separately and "
                                "record its path: bidoc config --pbi-tools C:\\path\\to\\pbi-tools.exe")})
    if selection:
        checks.append({"check": "pbix-backend", "ok": selection.available,
                       "detail": (selection.backend or "unavailable") + (" (fallback)" if selection.fell_back else ""),
                       "fix": selection.note or selection.reason})
    if windows:
        wv2 = webview2_version()
        checks.append({"check": "webview2", "ok": bool(wv2), "detail": wv2 or "not found",
                       "fix": None if wv2 else "Install the Microsoft Edge WebView2 Runtime (needed by the desktop "
                                               "window only; the command line works without it)."})
    desktop = _power_bi_desktop()
    checks.append({"check": "power-bi-desktop", "ok": bool(desktop), "detail": desktop or "not found",
                   "fix": None if desktop else "Install Power BI Desktop (required by pbi-tools to read PBIX files)."})
    ready["pbix"] = {"available": bool(selection and selection.available),
                     "reason": None if selection and selection.available else
                     (selection.reason if selection else "pbi-doc-gen is not installed")}
    abf_ok = pbixray_state["ok"] and pbi_ok
    ready["abf"] = {"available": abf_ok,
                    "reason": None if abf_ok else (pbixray_state["reason"] or "pbi-doc-gen is not installed")}
    return {"platform": f"{platform.system()} {platform.release()}", "checks": checks, "inputs": ready,
            "pbi_tools": selection.tool if selection else None,
            "pbix_backend": selection.as_dict() if selection else None}


def home() -> Path:
    """Where the generator keeps history, workspaces and identity mappings."""
    configured = os.environ.get("BIDOC_HOME")
    if configured:
        return Path(configured)
    if platform.system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "bidoc"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "bidoc"
