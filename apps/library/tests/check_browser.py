"""Start a real library with linked Power BI and ADF documents and run the browser
acceptance script (tests/browser_e2e.cjs) against it.

    python apps/library/tests/check_browser.py        (needs Node and the frontend npm packages)
"""
import json
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
sys.path.insert(0, str(HERE))

from fixtures import adf_factory  # noqa: E402
from test_derived import pbi_model  # noqa: E402

from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_library.api import create_app  # noqa: E402
from bidoc_library.config import Settings  # noqa: E402


def main():
    import uvicorn
    tmp = Path(tempfile.mkdtemp())
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        app = create_app(Settings(local_data_dir=tmp / "data", port=port))
        store = app.state.store

        def build(engine, source, kind, title):
            r = generate(GenerateRequest(engine=engine, source_path=str(source), source_kind=kind, title=title,
                                         output_dir=str(tmp / "out"), environment="Production", profile="shared"))
            assert r.status == "completed", r.errors
            return Path(r.artifact_path)

        adf = store.publish(build("adf", adf_factory(tmp / "factory"), "adf_git", "Test factory").read_bytes(),
                            subject="setup", idempotency_key="adf")
        pbi = store.publish(build("power_bi", pbi_model(tmp / "pbi"), "bim", "Sales report").read_bytes(),
                            subject="setup", idempotency_key="pbi")
        extra = build("adf", adf_factory(tmp / "other"), "adf_git", "Another factory")
        from bidoc_engines import adf as adf_engine        # an older document: engine HTML, no manifest
        old = tmp / "old-factory.html"
        old.write_text(adf_engine.render(adf_engine.load(adf_factory(tmp / "old"), "adf_git")), encoding="utf-8")
        from bidoc_library.access import Principal
        worker = app.state.jobs.enroll("E2E worker", None, Principal("setup", frozenset({"admin"})))
        processed = build("power_bi", pbi_model(tmp / "processed"), "bim", "Processed report")
        fake_pbix = tmp / "Quarterly.pbix"
        fake_pbix.write_bytes(b"PK\x03\x04 stand-in PBIX for the upload screen")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health/ready")
                break
            except OSError:
                time.sleep(0.1)
        env = dict(os.environ, LIB_URL=f"http://127.0.0.1:{port}", PBI_DOC=pbi["document_id"],
                   ADF_DOC=adf["document_id"], IMPORT_FILE=str(extra), LEGACY_FILE=str(old),
                   WORKER_TOKEN=worker["token"], PROCESS_FILE=str(fake_pbix), PROCESS_RESULT=str(processed),
                   NODE_PATH=str(ROOT / "apps" / "library" / "frontend" / "node_modules"))
        if Path("/opt/pw-browsers/chromium").exists() and "CHROMIUM_PATH" not in env:
            env["CHROMIUM_PATH"] = "/opt/pw-browsers/chromium"
        result = subprocess.run(["node", str(HERE / "browser_e2e.cjs")], env=env,
                                cwd=ROOT / "apps" / "library" / "frontend")
        server.should_exit = True
        thread.join(timeout=10)
        return result.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
