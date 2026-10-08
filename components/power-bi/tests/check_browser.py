"""Build the browser fixture and drive the generated document in real Chromium.

    python components/power-bi/tests/check_browser.py

Needs Node and Playwright with Chromium (as the `frontend` CI job installs them for apps/library/frontend).
Runs the long-standing regression script, the search and object-navigation checks and the Power Query view checks.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

from build_browser_fixture import build  # noqa: E402

SCRIPTS = ("browser_review.cjs", "browser_navigation.cjs", "browser_power_query.cjs")


def main():
    with tempfile.TemporaryDirectory() as folder:
        fixture = build(folder)
        env = dict(os.environ, PYTHON=sys.executable)
        modules = ROOT / "apps" / "library" / "frontend" / "node_modules"
        if modules.is_dir():
            env["NODE_PATH"] = str(modules)
        if Path("/opt/pw-browsers/chromium").exists() and "CHROMIUM_PATH" not in env:
            env["CHROMIUM_PATH"] = "/opt/pw-browsers/chromium"
        for script in SCRIPTS:
            print(script, flush=True)
            result = subprocess.run(["node", str(HERE / script), str(fixture)], env=env)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
