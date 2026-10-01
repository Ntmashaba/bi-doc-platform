"""Serve the generator desktop app (no window) and drive it with Chromium (tests/desktop_e2e.cjs).

    python apps/generator/tests/check_desktop_browser.py     (needs Node and apps/library/frontend packages)
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from bidoc_generator.batch import Runner  # noqa: E402
from bidoc_generator.desktop.app import create_app  # noqa: E402
from bidoc_generator.doctor import diagnose  # noqa: E402
from bidoc_generator.history import History  # noqa: E402


def main():
    import uvicorn
    tmp = Path(tempfile.mkdtemp())
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        report = diagnose(str(tmp / "no-pbi-tools.exe"))
        runner = Runner(History(tmp / "home"), pbix_ready=report["inputs"]["pbix"]["reason"])
        app = create_app(runner, session_secret="e2e", port=port, doctor=lambda: report)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            try:
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/state"))
                break
            except OSError:
                time.sleep(0.1)
        pbix = tmp / "Sales.pbix"
        pbix.write_bytes(b"PK")
        folder = tmp / "Report Folder"                       # a container: two PBIX files, one nested, and a factory
        for name in ("Sub/Alpha.pbix", "Sub/Deeper/Beta.pbix"):
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(b"PK")
        adf_factory(folder / "Factory")
        env = dict(os.environ, APP_URL=f"http://127.0.0.1:{port}", FACTORY=str(adf_factory(tmp / "factory")),
                   MISSING=str(tmp / "missing-factory"), PBIX=str(pbix), FOLDER=str(folder), OUT_DIR=str(tmp / "out"),
                   NODE_PATH=str(ROOT / "apps" / "library" / "frontend" / "node_modules"))
        if Path("/opt/pw-browsers/chromium").exists() and "CHROMIUM_PATH" not in env:
            env["CHROMIUM_PATH"] = "/opt/pw-browsers/chromium"
        result = subprocess.run(["node", str(HERE / "desktop_e2e.cjs")], env=env)
        runner.shutdown()
        server.should_exit = True
        thread.join(timeout=10)
        return result.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
