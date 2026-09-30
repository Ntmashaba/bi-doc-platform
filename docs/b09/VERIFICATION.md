# B09 verification report: Windows installer

Status: **built and verified on Windows CI (30 of 30 checks). Clean-machine run (A13)
waiting on the owner.** Since then the script also generates the public ABF backup (`-AbfArchive`); see `docs/validation/windows-validation-pack.md`.

## What is built

`packaging/windows/build.ps1` builds everything from the repository root on Windows with
Python 3.11:

- **Frozen program folder** (PyInstaller 6.22.3, `packaging/windows/bidoc.spec`). It holds
  `bidoc.exe` (console) and `BI Documentation Generator.exe` (windowed desktop app).
  - Python, both engines and their templates, the contract schema, the desktop UI and
    pywebview are bundled.
  - Build tools are pinned exactly in `requirements-windows-build.txt`.
- **Installer** (Inno Setup 6, `packaging/windows/installer.iss`):
  `bidoc-setup-<version>-unsigned.exe`.
  - Per-user: installs into `%LOCALAPPDATA%\Programs\BI Documentation Generator` with no
    administrator rights.
  - A fixed AppId, so a newer version upgrades in place. The old program files are
    replaced; user data is untouched.
  - Uninstall removes the program and the Start menu entry. It keeps `%LOCALAPPDATA%\bidoc`
    (history, settings, workspaces) unless the person chooses to delete it; silent
    uninstall keeps it. Generated documentation is never deleted.
  - The wizard, file names and version metadata say **unsigned development build**.
- **`release.json`**: version, commit, component versions, installer size, SHA-256, and the
  prerequisites that are not bundled.
- **`SHA256SUMS.txt`** and **`RELEASE-NOTES.md`**.

### Why Inno Setup

- It produces one setup executable that handles install, upgrade and uninstall.
- It supports per-user installs without elevation, and silent flags for automation.
- It is free for commercial use. The build uses the runner's copy, or installs it with Chocolatey when missing.
- MSIX would need signing even for testing. WiX adds an MSI toolchain with no benefit at
  this stage.

### Not bundled, by decision

Each is checked by `bidoc doctor` and the desktop app's Prerequisites page:

- Power BI Desktop and pbi-tools Desktop (AGPL-3.0, ADR 0001). Needed only for `.pbix`
  files. Record pbi-tools with `bidoc config --pbi-tools PATH`, which is kept in
  `config.json` across upgrades.
- The Microsoft Edge WebView2 Runtime. Needed only by the desktop window; it ships with
  current Windows 10 and 11.

## Acceptance script (A13)

`packaging/windows/verify-install.ps1` runs from a shell with no Python on `PATH` and
records every check in a JSON report:

1. Silent install; per-user uninstall registration; executables; Start menu entry; file
   version metadata.
2. `bidoc --version` and `doctor`: PBIP and ADF are ready, and PBIX readiness is explained.
3. A batch generating a PBIP project and an ADF Git folder (static inputs in
   `packaging/windows/verify-inputs`) as shared documents with a publication manifest, then
   local PBIP generation.
4. Settings saved (`bidoc config`).
5. The desktop executable starts. The desktop app serves its UI on 127.0.0.1 only and
   refuses changes without the session secret.
6. Upgrade to a newer installer: one registration; program files replaced; history,
   settings and generation still work.
7. Silent uninstall: program and Start menu entry removed; history, settings and generated
   documents kept.

## Results

| Run | Machine | Result |
|---|---|---|
| CI `windows-installer`, 2026-09-28, commit `9d2c32f` | GitHub `windows-latest` (Windows Server 2025); Python 3.11.9 on disk, not on `PATH`; WebView2 153.0.4234.48 | **30 of 30 checks passed.** The installer is 16.8 MB. The executables ran without Python on `PATH`. PBIX was correctly reported unavailable (no pbi-tools or Power BI Desktop). The upgrade 0.2.0 → 0.2.1 kept history and settings. Uninstall kept user data and the generated documents. The first run's only failure was the script's own `py.exe` check (the launcher sits in the Windows folder); fixed in `9d2c32f` |
| Clean machine (A13) | Clean Windows 10/11, no Python or Docker | **Not yet run.** Owner action: download the `bidoc-windows-installer` CI artifact, then run `verify-install.ps1 -Installer bidoc-setup-0.2.0-unsigned.exe` (add `-UpgradeInstaller` with a newer build to cover upgrade) and share the report |

## Not verified

- **Opening the desktop window.** CI starts the windowed executable and checks pywebview
  and WebView2, but no one looks at a window. The W1 probe showed pywebview runs on
  Windows. Checking the window visually is part of the owner's clean-machine run.
- **PBIX extraction with Power BI Desktop (W2).**
- **Code signing.** It needs the client's signing setup and is a release gate. These
  builds are labelled unsigned.
