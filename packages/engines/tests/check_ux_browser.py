"""Generate an ADF and a SQL Agent document and drive them in Chromium against the UX contract.

    python packages/engines/tests/check_ux_browser.py

The Power BI side of the contract runs in components/power-bi/tests/check_browser.py (browser_ux_contract.cjs).
Needs Node and Playwright with Chromium (as the `frontend` CI job installs them for apps/library/frontend).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

from fixtures import adf_factory  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402

SQL_AGENT_EXAMPLE = ROOT / "components" / "sql-agent" / "examples" / "example_jobs.sql"


def main() -> int:
    env = dict(os.environ)
    modules = ROOT / "apps" / "library" / "frontend" / "node_modules"
    if modules.is_dir():
        env["NODE_PATH"] = str(modules)
    if Path("/opt/pw-browsers/chromium").exists() and "CHROMIUM_PATH" not in env:
        env["CHROMIUM_PATH"] = "/opt/pw-browsers/chromium"
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        for engine, kind, source, script in (
                ("adf", "adf_git", adf_factory(tmp / "factory"), "browser_ux_adf.cjs"),
                ("sql_agent", "agent_script", SQL_AGENT_EXAMPLE, "browser_ux_sql_agent.cjs")):
            result = generate(GenerateRequest(engine=engine, source_path=str(source), source_kind=kind,
                                              output_dir=str(tmp / engine), profile="local"))
            if result.status != "completed":
                print(f"{engine}: generation failed: {result.errors}")
                return 1
            print(script, flush=True)
            run = subprocess.run(["node", str(HERE / script), str(result.artifact_path)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            print(run.stdout, end="", flush=True)
            if run.returncode:
                if os.environ.get("GITHUB_ACTIONS"):
                    tail = "\n".join(run.stdout.strip().splitlines()[-30:])[-3000:]
                    print(f"::error title={script}::" + tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
                return run.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
