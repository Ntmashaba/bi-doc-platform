"""msdb..proc (default schema left out) and literal-only concatenation in @command. Synthetic scripts only."""

from __future__ import annotations

from pathlib import Path

from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path


def analysed(tmp_path: Path, script: str):
    (tmp_path / "jobs.sql").write_text(script, encoding="utf-8")
    return analyse(load_path(tmp_path))


def test_msdb_double_dot_procedures_are_recognised(tmp_path):
    for prefix in ("msdb..", "[msdb]..", "msdb.dbo.", "dbo.", ""):
        p = analysed(tmp_path, f"EXEC {prefix}sp_add_job @job_name = N'J'\n"
                               f"EXEC {prefix}sp_add_jobstep @job_name = N'J', @step_name = N's', @command = N'SELECT 1'\n"
                               f"EXEC {prefix}sp_add_jobserver @job_name = N'J'\n")
        assert [(j["name"], len(j["steps"]), j["registeredOn"]) for j in p["jobs"]] == [("J", 1, ["(local)"])], prefix
        assert p["coverage"]["unrecognisedStatements"] == 0, prefix


def step_of(tmp_path, command_sql):
    p = analysed(tmp_path, "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n"
                           f"EXEC msdb.dbo.sp_add_jobstep @job_name = N'J', @step_name = N's', @command = {command_sql}\n")
    return p["jobs"][0]["steps"][0], p["coverage"]["unparsedSteps"]


def test_concatenated_literals_are_one_known_command(tmp_path):
    step, unparsed = step_of(tmp_path, "N'INSERT INTO dbo.Fact ' + N'SELECT * FROM ' + 'dbo.Stage'")
    assert (step["commandKind"], step["command"], unparsed) == (
        "literal", "INSERT INTO dbo.Fact SELECT * FROM dbo.Stage", 0)
    assert step["sql"] == {"reads": ["dbo.Stage"], "writes": ["dbo.Fact"], "procs": []}


def test_doubled_quotes_survive_the_fold(tmp_path):
    step, _ = step_of(tmp_path, "N'SELECT ''a'' ' + N'AS x'")
    assert step["command"] == "SELECT 'a' AS x"


def test_anything_but_literals_stays_unparsed(tmp_path):
    for expr in ("N'EXEC dbo.p ' + @days + N';'", "N'SELECT ' + CAST(1 AS nvarchar(9))", "N'a' + 1"):
        step, unparsed = step_of(tmp_path, expr)
        assert (step["commandKind"], unparsed) == ("expression", 1), expr


def test_a_declared_concatenation_is_tracked_like_a_literal(tmp_path):
    p = analysed(tmp_path, "DECLARE @c nvarchar(max) = N'SELECT 1 ' + N'FROM dbo.T'\n"
                           "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n"
                           "EXEC msdb.dbo.sp_add_jobstep @job_name = N'J', @step_name = N's', @command = @c\n")
    step = p["jobs"][0]["steps"][0]
    assert (step["commandKind"], p["coverage"]["unparsedSteps"]) == ("literal", 0)
    assert step["sql"]["reads"] == ["dbo.T"]
