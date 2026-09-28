# BI Documentation Generator {{VERSION}}

**{{LABEL}}.** This installer is not code-signed; Windows SmartScreen may warn before it
runs. Production releases are signed, and signing is a release gate.

- Installer: `{{INSTALLER}}`
- SHA-256: `{{SHA256}}`
- Source commit: `{{COMMIT}}`
- Bundled: Python {{PYTHON}}, pbi-doc-gen {{PBIDOCGEN}}, adf-doc-gen {{ADFDOCGEN}}

## Install, upgrade, uninstall

- Per-user install, with no administrator rights, into
  `%LOCALAPPDATA%\Programs\BI Documentation Generator`. The Start menu gets the desktop app;
  `bidoc.exe` in the same folder is the command line.
- Installing a newer version over an older one upgrades in place.
- History, settings and workspaces live in `%LOCALAPPDATA%\bidoc`. Upgrades keep them, and
  uninstall keeps them unless you choose otherwise. Generated documentation is never deleted.
- Silent use: `bidoc-setup-….exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`; uninstall with
  `unins000.exe /VERYSILENT` in the install folder.

## Not bundled

These are installed separately:
- Power BI Desktop and pbi-tools Desktop (AGPL-3.0), needed for `.pbix` files only;
- the Microsoft Edge WebView2 Runtime, needed by the desktop window.

`bidoc doctor` reports what is missing and how to fix it. PBIP, TMDL/BIM, PBIR and Data
Factory inputs need none of them.
