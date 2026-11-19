"""The built wheel ships template.html and installs a working CLI.

Editable installs cannot detect a missing package-data declaration — the
template is always present in the source tree.  This test builds the real
wheels (sqlagent-doc-gen plus its adf-doc-gen dependency), installs them
into a throwaway virtual environment with --no-deps, and renders a document
from outside the repository: renderer.py reads template.html from the
installed package, so a wheel without it fails here.

Builds offline (bundled pip/setuptools, no build isolation).
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def _clean_env():
    # never let the venv's python see the source tree: the point of this test
    # is that the installed wheels stand on their own
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


ROOT = Path(__file__).resolve().parents[1]
ADF = ROOT.parent / "adf"

SCRIPT = """\
EXEC msdb.dbo.sp_add_job @job_name = N'Wheel Smoke', @enabled = 1
EXEC msdb.dbo.sp_add_jobstep @job_name = N'Wheel Smoke', @step_id = 1,
    @step_name = N'only', @subsystem = N'TSQL', @command = N'SELECT 1'
"""


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wheel")
    subprocess.run([sys.executable, "-m", "venv", str(tmp / "venv")],
                   check=True, capture_output=True)
    bin_dir = tmp / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    python = next(p for p in bin_dir.iterdir() if p.stem == "python")
    for component in (ADF, ROOT):
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                        "--no-build-isolation", "-w", str(tmp / "dist"),
                        str(component)], check=True, capture_output=True,
                       env=_clean_env())
    wheels = sorted((tmp / "dist").glob("*.whl"))
    assert len(wheels) == 2, [w.name for w in wheels]
    for wheel in wheels:
        subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
                       check=True, capture_output=True, env=_clean_env())
    return tmp, python, wheels


def test_wheel_contains_template(installed):
    _, _, wheels = installed
    wheel = next(w for w in wheels if w.name.startswith("sqlagent_doc_gen"))
    names = set(zipfile.ZipFile(wheel).namelist())
    assert "sqlagentdocgen/template.html" in names, \
        "template.html missing from the wheel (package-data not declared)"


def test_installed_cli_renders_html_outside_repository(installed, tmp_path):
    tmp, python, _ = installed
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    src = cwd / "jobs.sql"
    src.write_text(SCRIPT, encoding="utf-8")
    out = cwd / "docs.html"
    run = subprocess.run([str(python), "-m", "sqlagentdocgen", str(src),
                          "--html", str(out)], cwd=cwd, env=_clean_env(),
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "const DATA = " in out.read_text(encoding="utf-8")
