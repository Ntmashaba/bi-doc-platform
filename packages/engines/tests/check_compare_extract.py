"""`bidoc generate --also json` gives "Compare extracts" a baseline: generate, change a measure, generate again, and
load the first extract into the second document in Chromium.

    python packages/engines/tests/check_compare_extract.py

Needs Node and Playwright with Chromium (as the `frontend` CI job installs them for apps/library/frontend).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

from fixtures import pbi_model  # noqa: E402
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
        model = pbi_model(tmp / "model")
        for profile in ("local", "shared"):
            first = generate(GenerateRequest(engine="power_bi", source_path=str(model), source_kind="bim",
                                             output_dir=str(tmp / f"first-{profile}"), profile=profile, also=("json",)))
            baseline = next(p for p in first.extra_paths if p.endswith(".json"))
            doc = json.loads(model.read_text(encoding="utf-8"))
            for table in doc["model"]["tables"]:
                for measure in table.get("measures", []):
                    if measure["name"] == "Revenue":
                        measure["expression"] = "SUMX(Sales, Sales[Amount] * 1.15)"
            changed = tmp / f"changed-{profile}" / "model.bim"
            changed.parent.mkdir()
            changed.write_text(json.dumps(doc), encoding="utf-8")
            second = generate(GenerateRequest(engine="power_bi", source_path=str(changed), source_kind="bim",
                                              output_dir=str(tmp / f"second-{profile}"), profile=profile))
            if first.status != "completed" or second.status != "completed":
                print(f"{profile}: generation failed: {first.errors or second.errors}")
                return 1
            run = subprocess.run(["node", str(HERE / "browser_compare_extract.cjs"), second.artifact_path, baseline],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            print(f"{profile}: " + run.stdout, end="", flush=True)
            if run.returncode:
                if os.environ.get("GITHUB_ACTIONS"):
                    tail = "\n".join(run.stdout.strip().splitlines()[-30:])[-3000:]
                    print("::error title=browser_compare_extract.cjs::" + tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
                return run.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
