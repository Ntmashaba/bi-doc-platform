"""Review round 4 regressions. Synthetic scripts only; each names no real system."""

from __future__ import annotations

from pathlib import Path

import pytest

from sqlagentdocgen import cli
from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path
from sqlagentdocgen.redact import REDACTED, scrub_text
from sqlagentdocgen.script_parser import parse_calls
from sqlagentdocgen.tokenizer import lex


# -- -P: glued only on sqlcmd / bcp / osql lines; PowerShell parameters are not passwords --------------------------

@pytest.mark.parametrize("text", [
    "Set-Location -Path 'C:\\jobs'",
    "Copy-Item -Path $src -Destination $dst -PassThru",
    "Get-ChildItem -Path . -Recurse | Remove-Item -Path {$_.FullName}",
    "Invoke-Sqlcmd -ServerInstance s -Port 1433 -Query 'SELECT 1'",      # a cmdlet, not the sqlcmd tool
    "Invoke-Sqlcmd -ServerInstance s -Database d -InputFile x.sql -PassThru",
    "Test-NetConnection -ComputerName s -Port 2383",
])
def test_powershell_parameters_are_untouched(text):
    assert scrub_text(text) == (text, 0)


@pytest.mark.parametrize("text, expected", [
    ("sqlcmd -S s -U u -PSecret -Q x", f"sqlcmd -S s -U u -P{REDACTED} -Q x"),
    ("C:\\Tools\\SQLCMD.EXE -S s -PSecret", f"C:\\Tools\\SQLCMD.EXE -S s -P{REDACTED}"),
    ("bcp db..t out f.dat -S s -U u -PSecret -c", f"bcp db..t out f.dat -S s -U u -P{REDACTED} -c"),
    ('osql -S s -U u -P"two words"', f"osql -S s -U u -P{REDACTED}"),
    ("sqlcmd -S s -U u -P Secret -Q x", f"sqlcmd -S s -U u -P {REDACTED} -Q x"),
    ("tool.exe -P Secret", f"tool.exe -P {REDACTED}"),                     # the spaced form anywhere, as before
])
def test_sql_tool_passwords_are_still_redacted(text, expected):
    assert scrub_text(text) == (expected, 1)
    assert scrub_text(expected) == (expected, 0)


def test_glued_form_is_per_line():
    script = "sqlcmd -S s -PSecret -Q x\nSet-Location -Path 'C:\\jobs'"
    assert scrub_text(script) == (f"sqlcmd -S s -P{REDACTED} -Q x\nSet-Location -Path 'C:\\jobs'", 1)


# -- unquoted values: whole in a connection string, one token in T-SQL; NULL is no secret ---------------------------

@pytest.mark.parametrize("text, expected", [
    ("UPDATE dbo.Cfg SET secret = NULL WHERE id = 1", ("UPDATE dbo.Cfg SET secret = NULL WHERE id = 1", 0)),
    ("UPDATE dbo.Cfg SET pwd = 0x01AB WHERE id = 1", (f"UPDATE dbo.Cfg SET pwd = {REDACTED} WHERE id = 1", 1)),
    ("CREATE LOGIN x WITH PASSWORD = 0x0200AB HASHED, CHECK_POLICY = OFF",
     (f"CREATE LOGIN x WITH PASSWORD = {REDACTED} HASHED, CHECK_POLICY = OFF", 1)),
    ("Server=a;Password=two words, and more;Database=d", (f"Server=a;Password={REDACTED};Database=d", 1)),
    ("Server=a;Password=two words at the end", (f"Server=a;Password={REDACTED}", 1)),
    ("Password=first words;Server=a", (f"Password={REDACTED};Server=a", 1)),
])
def test_unquoted_values(text, expected):
    assert scrub_text(text) == expected


# -- CASE inside SET / DECLARE is an expression ---------------------------------------------------------------------

@pytest.mark.parametrize("prelude", [
    "DECLARE @x INT\nSET @x = CASE WHEN 1 = 1 THEN 1 ELSE 0 END\n",
    "DECLARE @x INT = CASE WHEN 1 = 1 THEN 1 ELSE 0 END\n",
    "DECLARE @x INT = CASE WHEN a = 1 THEN CASE WHEN b = 1 THEN 1 ELSE 2 END ELSE 3 END, @y INT = 5\n",
])
def test_case_in_an_assignment_is_not_control_flow(prelude):
    calls = parse_calls(lex(prelude + "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n"))
    assert [c.conditional for c in calls] == [False]


def test_case_in_an_assignment_inside_if_keeps_the_block(tmp_path):
    calls = parse_calls(lex("IF 1 = 1\nBEGIN\n  SET @x = CASE WHEN 1 = 1 THEN 1 ELSE 0 END\n"
                            "  EXEC msdb.dbo.sp_add_job @job_name = N'J'\nEND\n"
                            "EXEC msdb.dbo.sp_add_job @job_name = N'K'\n"))
    assert [c.conditional for c in calls] == [True, False]


def test_two_creations_after_a_case_assignment_are_duplicates(tmp_path):
    (tmp_path / "j.sql").write_text("DECLARE @x INT\nSET @x = CASE WHEN 1 = 1 THEN 1 ELSE 0 END\n"
                                    "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n"
                                    "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n", encoding="utf-8")
    p = analyse(load_path(tmp_path))
    assert [j["identity"] for j in p["jobs"]] == ["name:j", "name:j@j.sql"]
    assert "job-duplicate" in [i["code"] for i in p["issues"]]


def test_a_value_that_is_a_case_is_not_a_known_literal(tmp_path):
    (tmp_path / "j.sql").write_text("DECLARE @cmd nvarchar(max) = CASE WHEN 1 = 1 THEN N'EXEC a' ELSE N'EXEC b' END\n"
                                    "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n"
                                    "EXEC msdb.dbo.sp_add_jobstep @job_name = N'J', @step_name = N's', @command = @cmd\n",
                                    encoding="utf-8")
    p = analyse(load_path(tmp_path))
    assert (p["jobs"][0]["steps"][0]["commandKind"], p["coverage"]["unparsedSteps"]) == ("variable", 1)


# -- a deleted job leaves its schedules ------------------------------------------------------------------------------

def test_a_deleted_job_is_detached_from_shared_schedules(tmp_path):
    (tmp_path / "j.sql").write_text(
        "DECLARE @s INT\nEXEC msdb.dbo.sp_add_job @job_name = N'Keep'\nEXEC msdb.dbo.sp_add_job @job_name = N'Gone'\n"
        "EXEC msdb.dbo.sp_add_schedule @schedule_name = N'Nightly', @freq_type = 4, @freq_interval = 1, "
        "@schedule_id = @s OUTPUT\n"
        "EXEC msdb.dbo.sp_attach_schedule @job_name = N'Keep', @schedule_id = @s\n"
        "EXEC msdb.dbo.sp_attach_schedule @job_name = N'Gone', @schedule_id = @s\n"
        "EXEC msdb.dbo.sp_delete_job @job_name = N'Gone'\n", encoding="utf-8")
    p = analyse(load_path(tmp_path))
    assert p["sharedSchedules"][0]["attachedJobs"] == ["Keep"]
    assert [j["name"] for j in p["jobs"]] == ["Keep"]


# -- a parser failure names its exception on the console, not in the document ----------------------------------------

def test_parser_failure_detail_reaches_the_console_only(tmp_path, capsys, monkeypatch):
    import sqlagentdocgen.analyzer as analyzer

    real = analyzer.parse_calls

    def failing(toks, bindings_out=None):
        if any(t.text == "Broken" for t in toks):
            raise IndexError("simulated parser bug")
        return real(toks, bindings_out=bindings_out)
    monkeypatch.setattr(analyzer, "parse_calls", failing)
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.sql").write_text("EXEC msdb.dbo.sp_add_job @job_name = N'Fine'\n", encoding="utf-8")
    (src / "b.sql").write_text("EXEC msdb.dbo.sp_add_job @job_name = N'J'\nSELECT Broken\n", encoding="utf-8")
    out = tmp_path / "doc.html"
    assert cli.main([str(src), "--html", str(out)]) == 0
    err = capsys.readouterr().err
    assert "IndexError: simulated parser bug" in err and "b.sql" in err
    html = out.read_text(encoding="utf-8")
    assert "b.sql: could not be analysed completely" in html
    assert "IndexError" not in html and str(tmp_path) not in html
