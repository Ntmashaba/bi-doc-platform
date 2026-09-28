"""Prerequisite diagnostics: what this machine can generate, and how to fix what it cannot."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path


def _pbi_tools(configured: str | None):
    """A configured path wins; otherwise PATH. Never search the disk and run what is found."""
    candidate = configured or os.environ.get("BIDOC_PBI_TOOLS")
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


def diagnose(pbi_tools: str | None = None) -> dict:
    checks, ready = [], {}
    py_ok = sys.version_info >= (3, 11)
    checks.append({"check": "python", "ok": py_ok, "detail": platform.python_version(),
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
    tools = _pbi_tools(pbi_tools)
    desktop = _power_bi_desktop()
    checks.append({"check": "pbi-tools", "ok": bool(tools), "detail": tools or "not found",
                   "fix": None if tools else "Install pbi-tools Desktop (https://pbi.tools, AGPL-3.0) separately and "
                                             "set BIDOC_PBI_TOOLS to its pbi-tools.exe path."})
    checks.append({"check": "power-bi-desktop", "ok": bool(desktop), "detail": desktop or "not found",
                   "fix": None if desktop else "Install Power BI Desktop (required by pbi-tools to read PBIX files)."})
    reasons = [r for r, bad in (("PBIX extraction runs on Windows only", not windows),
                                ("pbi-tools not found", not tools),
                                ("Power BI Desktop not found", not desktop)) if bad]
    if not pbi_ok:
        reasons.append("pbi-doc-gen is not installed")
    ready["pbix"] = {"available": not reasons, "reason": "; ".join(reasons) or None}
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
