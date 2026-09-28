"""Build two portable exports (complete and partial) and drive them over file:// (A12, A38).

    python apps/generator/tests/check_offline_browser.py [--channel msedge]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
sys.path.insert(0, str(ROOT / "apps" / "library" / "tests"))
from fixtures import adf_factory  # noqa: E402
from test_derived import pbi_model  # noqa: E402

from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_generator.export import export_library  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        out = tmp / "out"
        ids = {}
        for engine, src, kind in (("adf", adf_factory(tmp / "factory"), "adf_git"),
                                  ("power_bi", pbi_model(tmp / "pbi"), "bim")):
            r = generate(GenerateRequest(engine=engine, source_path=str(src), source_kind=kind, output_dir=str(out),
                                         environment="Production"))
            assert r.status == "completed", r.errors
            ids[engine] = r.document_id
        export_library([out], tmp / "portable")
        export_library([out], tmp / "partial", only=[ids["adf"]])
        env = dict(os.environ, EXPORT_DIR=str(tmp / "portable"), PARTIAL_DIR=str(tmp / "partial"),
                   ADF_ID=ids["adf"], PBI_ID=ids["power_bi"],
                   NODE_PATH=str(ROOT / "apps" / "library" / "frontend" / "node_modules"))
        if "--channel" in sys.argv:
            env["BROWSER_CHANNEL"] = sys.argv[sys.argv.index("--channel") + 1]
        elif Path("/opt/pw-browsers/chromium").exists() and "CHROMIUM_PATH" not in env:
            env["CHROMIUM_PATH"] = "/opt/pw-browsers/chromium"
        return subprocess.run(["node", str(HERE / "offline_e2e.cjs")], env=env).returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
