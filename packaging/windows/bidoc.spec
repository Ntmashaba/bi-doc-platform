# PyInstaller spec: bidoc.exe (console) and "BI Documentation Generator.exe" (windowed)
# sharing one folder. Build: pyinstaller --noconfirm packaging/windows/bidoc.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

HERE = SPECPATH  # noqa: F821 (provided by PyInstaller)

datas, hidden = [], []
for package in ("pbidocgen", "adfdocgen", "bidoc_contracts", "bidoc_engines", "bidoc_relationships",
                "bidoc_generator"):
    datas += collect_data_files(package)             # engine templates, schema, desktop static files
    hidden += collect_submodules(package)            # engines import some modules lazily
# Portable PBIX/ABF extraction: pbixray is imported lazily and pbidocgen.portable checks its installed version
# through importlib.metadata, so its distribution metadata must ship with the executable.
datas += copy_metadata("pbixray") + collect_data_files("pbixray")
hidden += collect_submodules("pbixray")
hidden += collect_submodules("uvicorn") + ["webview.platforms.edgechromium", "webview.platforms.winforms"]
excludes = ["tkinter", "test"]


def analysis(script):
    return Analysis([f"{HERE}/{script}"], datas=datas, hiddenimports=hidden, excludes=excludes,  # noqa: F821
                    noarchive=False)


cli, gui = analysis("entry_cli.py"), analysis("entry_desktop.py")
common = dict(debug=False, strip=False, upx=False, exclude_binaries=True, version=f"{HERE}/version_info.txt")
cli_exe = EXE(PYZ(cli.pure), cli.scripts, name="bidoc", console=True, **common)  # noqa: F821
gui_exe = EXE(PYZ(gui.pure), gui.scripts, name="BI Documentation Generator", console=False, **common)  # noqa: F821
COLLECT(cli_exe, cli.binaries, cli.datas, gui_exe, gui.binaries, gui.datas,  # noqa: F821
        strip=False, upx=False, name="bidoc")
