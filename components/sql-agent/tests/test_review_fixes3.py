"""Regression tests for the third round of review findings.

Covers: CSV formula-injection neutralization (script-controlled strings must
not become spreadsheet formulas) and the msdb start-step default (a job with
no @start_step_id starts at step ID 1, not at the first step in the script;
when no step 1 exists the start is reported as undetermined, never guessed).

Standalone: synthetic scripts only (v4 §10).
"""

from __future__ import annotations

import csv

from sqlagentdocgen import cli
from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path
from sqlagentdocgen.renderer import build_payload


def _payload(tmp_path, text, title="t"):
    f = tmp_path / "jobs.sql"
    f.write_text(text, encoding="utf-8")
    return build_payload(analyse(load_path(f)), title)


# -- CSV formula injection --------------------------------------------------

def test_csv_cells_neutralize_formula_injection(tmp_path):
    payload = _payload(tmp_path, """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'=HYPERLINK("http://evil.example")', @job_id = @jobId OUTPUT,
    @owner_login_name = N'EXAMPLE\\svc'
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 1, @step_name = N'+cmd',
    @subsystem = N'TSQL', @database_name = N'Ops', @command = N'SELECT 1'
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 2, @step_name = N'  -2+3',
    @subsystem = N'TSQL', @database_name = N'Ops', @command = N'SELECT 2'
EXEC msdb.dbo.sp_add_jobschedule @job_id = @jobId, @name = N'@SUM(1)',
    @freq_type = 4, @freq_interval = 1, @freq_subday_type = 1, @active_start_time = 220000
""")
    folder = tmp_path / "csv"
    cli.write_csv(payload, folder)
    for path in folder.glob("*.csv"):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.reader(fh):
                for cell in row:
                    i = 0
                    while i < len(cell) and (cell[i].isspace() or ord(cell[i]) < 32):
                        i += 1
                    assert i >= len(cell) or cell[i] not in "=+-@", \
                        f"formula-like cell in {path.name}: {cell!r}"
    # the text itself is preserved behind the apostrophe prefix
    with open(folder / "jobs.csv", newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert rows[1][0] == '\'=HYPERLINK("http://evil.example")'


def test_csv_cell_helper_leaves_ordinary_values_alone():
    assert cli._csv_cell("Nightly Cube") == "Nightly Cube"
    assert cli._csv_cell("a - b") == "a - b"        # not a leading trigger char
    assert cli._csv_cell(5) == 5
    assert cli._csv_cell(None) is None
    assert cli._csv_cell("") == ""
    assert cli._csv_cell("\x01=1+1") == "'\x01=1+1"  # control-char prefix stripped for the check


# -- start-step default -----------------------------------------------------

def test_default_start_is_step_id_1_not_script_order(tmp_path):
    f = tmp_path / "jobs.sql"
    f.write_text("""
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'OutOfOrder', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 2, @step_name = N'second',
    @command = N'SELECT 2', @on_success_action = 1, @on_fail_action = 2
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 1, @step_name = N'first',
    @command = N'SELECT 1', @on_success_action = 1, @on_fail_action = 2
""", encoding="utf-8")
    g = analyse(load_path(f))["executionGraphs"]["name:outoforder"]
    # msdb starts at step ID 1 even though the script added step 2 first
    assert g["start"] == "1"
    assert g["startNote"] is None


def test_start_undetermined_when_no_step_1_and_no_start_step_id(tmp_path):
    f = tmp_path / "jobs.sql"
    f.write_text("""
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'NoStart', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 5, @step_name = N'only',
    @command = N'SELECT 1', @on_success_action = 1, @on_fail_action = 2
""", encoding="utf-8")
    g = analyse(load_path(f))["executionGraphs"]["name:nostart"]
    # the uncertainty is exposed; payload order is never substituted silently
    assert g["start"] is None
    assert "step with ID 1" in g["startNote"]
    assert g["unreachable"] == []  # no reachability claims without a start


def test_start_note_when_scripted_start_step_id_matches_no_step(tmp_path):
    f = tmp_path / "jobs.sql"
    f.write_text("""
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'BadStart', @job_id = @jobId OUTPUT, @start_step_id = 9
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 1, @step_name = N'one',
    @command = N'SELECT 1', @on_success_action = 1, @on_fail_action = 2
""", encoding="utf-8")
    g = analyse(load_path(f))["executionGraphs"]["name:badstart"]
    assert g["start"] is None
    assert "@start_step_id (9)" in g["startNote"]
    assert g["unreachable"] == []
