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


def _pbi_tools(configured: str | None):
    """A configured path wins (argument, BIDOC_PBI_TOOLS, config.json); otherwise PATH.
    Never search the disk and run what is found."""
    candidate = configured or os.environ.get("BIDOC_PBI_TOOLS") or config().get("pbi_tools")
    if candidate:
        return str(Path(candidate)) if Path(candidate).is_file() else None
    return shutil.which("pbi-tools") or shutil.which("pbi-tools.exe")


def _power_bi_desktop():
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


def _pbixray_status() -> dict:
    """Whether the portable (pbixray) reader can run here; see pbidocgen.portable.status."""
    try:
        from pbidocgen.portable import status  # noqa: PLC0415
    except ImportError:
        return {"installed": None, "supported": None, "ok": False, "reason": "pbi-doc-gen is not installed"}
    return status()


def diagnose(pbi_tools: str | None = None) -> dict:
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
    configured = _pbi_tools(pbi_tools)
    pbixray_state = _pbixray_status()
    portable = pbi_tools == "pbixray" and pbixray_state["ok"]   # opt-in: asked for explicitly, never a default
    tools = "pbixray" if portable else configured
    if pbixray_state["installed"] or pbi_tools == "pbixray":
        checks.append({"check": "pbixray", "ok": pbixray_state["ok"],
                       "detail": pbixray_state["installed"] or "not installed",
                       "fix": None if pbixray_state["ok"] else pbixray_state["reason"]})
    desktop = _power_bi_desktop()
    checks.append({"check": "pbi-tools", "ok": bool(tools), "detail": tools or "not found",
                   "fix": None if tools else "Install pbi-tools Desktop (https://pbi.tools, AGPL-3.0) separately and "
                                             "record its path: bidoc config --pbi-tools C:\\path\\to\\pbi-tools.exe"})
    if windows:
        wv2 = webview2_version()
        checks.append({"check": "webview2", "ok": bool(wv2), "detail": wv2 or "not found",
                       "fix": None if wv2 else "Install the Microsoft Edge WebView2 Runtime (needed by the desktop "
                                               "window only; the command line works without it)."})
    checks.append({"check": "power-bi-desktop", "ok": bool(desktop), "detail": desktop or "not found",
                   "fix": None if desktop else "Install Power BI Desktop (required by pbi-tools to read PBIX files)."})
    reasons = [r for r, bad in (("PBIX extraction runs on Windows only", not windows),
                                ("pbi-tools not found", not tools),
                                ("Power BI Desktop not found", not desktop)) if bad]
    if not pbi_ok:
        reasons.append("pbi-doc-gen is not installed")
    if portable and pbi_ok:
        reasons = []
    ready["pbix"] = {"available": not reasons, "reason": "; ".join(reasons) or None}
    abf_ok = pbixray_state["ok"] and pbi_ok
    ready["abf"] = {"available": abf_ok,
                    "reason": None if abf_ok else (pbixray_state["reason"] or "pbi-doc-gen is not installed")}
    return {"platform": f"{platform.system()} {platform.release()}", "checks": checks, "inputs": ready,
            "pbi_tools": tools}


def home() -> Path:
    """Where the generator keeps history, workspaces and identity mappings."""
    configured = os.environ.get("BIDOC_HOME")
    if configured:
        return Path(configured)
    if platform.system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "bidoc"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "bidoc"
