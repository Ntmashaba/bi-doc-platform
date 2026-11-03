"""Generate a manifest-bearing document from each engine, drive its details view in Chromium, and validate it
before and after: the artifact stays valid and byte-for-byte the same, and the page offers no copy to save.

    python packages/engines/tests/check_sealed_browser.py

Needs Node and Playwright with Chromium (as the `frontend` CI job installs them for apps/library/frontend).
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

from fixtures import adf_factory, pbi_model  # noqa: E402
from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402


def main() -> int:
    env = dict(os.environ)
    modules = ROOT / "apps" / "library" / "frontend" / "node_modules"
    if modules.is_dir():
        env["NODE_PATH"] = str(modules)
    if Path("/opt/pw-browsers/chromium").exists() and "CHROMIUM_PATH" not in env:
        env["CHROMIUM_PATH"] = "/opt/pw-browsers/chromium"
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        for engine, kind, source in (("power_bi", "bim", pbi_model(tmp / "model")), ("adf", "adf_git", adf_factory(tmp / "factory"))):
            for profile in ("local", "shared"):
                result = generate(GenerateRequest(engine=engine, source_path=str(source), source_kind=kind,
                                                  output_dir=str(tmp / f"{engine}-{profile}"), profile=profile))
                if result.status != "completed":
                    print(f"{engine} {profile}: generation failed: {result.errors}")
                    return 1
                path = Path(result.artifact_path)
                before = path.read_bytes()
                validate_artifact(before)
                run = subprocess.run(["node", str(HERE / "browser_sealed_details.cjs"), str(path), engine], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                print(run.stdout, end="", flush=True)
                if run.returncode:
                    if os.environ.get("GITHUB_ACTIONS"):
                        tail = "\n".join(run.stdout.strip().splitlines()[-30:])[-3000:]
                        print("::error title=browser_sealed_details.cjs::" + tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
                    return run.returncode
                after = path.read_bytes()
                assert hashlib.sha256(after).digest() == hashlib.sha256(before).digest(), f"{path} changed"
                validate_artifact(after)
                print(f"{engine} {profile}: artifact valid before and after, unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
