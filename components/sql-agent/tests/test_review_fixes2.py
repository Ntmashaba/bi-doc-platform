"""Regression tests for the second round of Phase 2 review findings.

Covers: batch-scoped variable identity (no cross-batch/file leaks),
monthly-relative schedule coincidence signatures, case-insensitive .sql
discovery, and sanitized coverage-only warnings.
"""

from __future__ import annotations

import os

from sqlagentdocgen import cli
from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path


# -- batch-scoped variable identity -----------------------------------------


def test_variable_identity_does_not_leak_across_batches(tmp_path):
    # Batch 1 captures @jobId for job A. Batch 2 redeclares @jobId and tries
    # to bind it to job B through an aliased sysjobs lookup — a form the
    # parser does not recognize. The step must NOT silently attach to A.
    (tmp_path / "j.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name=N'A', @job_id=@jobId OUTPUT\n"
        "GO\n"
        "DECLARE @jobId BINARY(16)\n"
        "SELECT @jobId = sj.job_id FROM msdb.dbo.sysjobs sj WHERE sj.name = N'B'\n"
        "EXEC msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_name=N'S',\n"
        " @subsystem=N'TSQL', @command=N'SELECT 1'\n",
        encoding="utf-8")
    p = analyse(load_path(tmp_path))
    job_a = [j for j in p["jobs"] if j["name"] == "A"][0]
    assert job_a["steps"] == []  # not silently attached to A
    assert any("@jobid" in w.lower() for w in p["warnings"])  # warned instead


def test_variable_identity_does_not_leak_across_files(tmp_path):
    (tmp_path / "a.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name=N'A', @job_id=@jobId OUTPUT\n",
        encoding="utf-8")
    (tmp_path / "b.sql").write_text(
        "EXEC msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_name=N'S'\n",
        encoding="utf-8")
    p = analyse(load_path(tmp_path))
    job_a = [j for j in p["jobs"] if j["name"] == "A"][0]
    assert job_a["steps"] == []
    assert any("@jobid" in w.lower() for w in p["warnings"])


def test_same_batch_capture_still_works(tmp_path):
    (tmp_path / "j.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name=N'A', @job_id=@jobId OUTPUT\n"
        "EXEC msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_name=N'S'\n",
        encoding="utf-8")
    p = analyse(load_path(tmp_path))
    assert [s["name"] for s in p["jobs"][0]["steps"]] == ["S"]


# -- schedule coincidence signature ------------------------------------------
# Coincidence is only between jobs that process the same SSAS database (v4 §6), so every job here processes
# SeededDW; the schedule signature alone decides.

_PROCESS = ("EXEC sp_add_jobstep @job_id=@{v}, @step_id=1, @step_name=N'p', @subsystem=N'ANALYSISCOMMAND',"
            " @server=N'SEEDED-SSAS-01', @command=N'<Batch xmlns=\"http://schemas.microsoft.com/analysisservices/"
            "2003/engine\"><Process><Type>ProcessFull</Type><Object><DatabaseID>SeededDW</DatabaseID></Object>"
            "</Process></Batch>'\n")


def _two_processing_jobs(tmp_path, sched1: str, sched2: str):
    (tmp_path / "j.sql").write_text(
        "EXEC sp_add_job @job_name=N'J1', @job_id=@j1 OUTPUT\n" + _PROCESS.format(v="j1")
        + f"EXEC sp_add_jobschedule @job_id=@j1, {sched1}\n"
        + "EXEC sp_add_job @job_name=N'J2', @job_id=@j2 OUTPUT\n" + _PROCESS.format(v="j2")
        + f"EXEC sp_add_jobschedule @job_id=@j2, {sched2}\n",
        encoding="utf-8")
    return {i["code"] for i in analyse(load_path(tmp_path))["issues"]}


def test_monthly_relative_schedules_do_not_coincide(tmp_path):
    # first Monday vs last Monday of the month at the same start time
    codes = _two_processing_jobs(
        tmp_path,
        "@name=N'First Monday', @freq_type=32, @freq_interval=2, @freq_relative_interval=1, @active_start_time=20000",
        "@name=N'Last Monday', @freq_type=32, @freq_interval=2, @freq_relative_interval=16, @active_start_time=20000")
    assert "schedules-coincide" not in codes


def test_identical_monthly_relative_schedules_still_coincide(tmp_path):
    codes = _two_processing_jobs(
        tmp_path,
        "@name=N'One', @freq_type=32, @freq_interval=2, @freq_relative_interval=1, @active_start_time=20000",
        "@name=N'Two', @freq_type=32, @freq_interval=2, @freq_relative_interval=1, @active_start_time=20000")
    assert "schedules-coincide" in codes


def test_active_window_separates_identical_frequencies(tmp_path):
    codes = _two_processing_jobs(
        tmp_path,
        "@name=N'One', @freq_type=4, @active_start_time=20000, @active_end_time=21000",
        "@name=N'Two', @freq_type=4, @active_start_time=20000, @active_end_time=22000")
    assert "schedules-coincide" not in codes


# -- case-insensitive discovery -----------------------------------------------


def test_uppercase_extension_is_discovered(tmp_path):
    (tmp_path / "JOB.SQL").write_text(
        "EXEC sp_add_job @job_name=N'Big'\n", encoding="utf-8")
    p = analyse(load_path(tmp_path))
    assert p["coverage"]["inputFiles"] == 1
    assert [j["name"] for j in p["jobs"]] == ["Big"]


def test_mixed_case_extension_is_discovered(tmp_path):
    (tmp_path / "job.Sql").write_text(
        "EXEC sp_add_job @job_name=N'Mixed'\n", encoding="utf-8")
    assert analyse(load_path(tmp_path))["coverage"]["inputFiles"] == 1


# -- sanitized coverage-only warnings ------------------------------------------


def test_coverage_only_sanitizes_load_errors(tmp_path, capsys):
    secret = tmp_path / "Customer Payroll"
    secret.mkdir()
    os.symlink(secret / "missing.sql", secret / "job.sql")  # broken symlink
    rc = cli.main(["--coverage-only", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Customer Payroll" not in out
    assert "Errno" not in out
    assert str(tmp_path) not in out
    assert "job.sql: could not be read" in out


def test_full_mode_keeps_error_detail(tmp_path, capsys):
    secret = tmp_path / "Customer Payroll"
    secret.mkdir()
    os.symlink(secret / "missing.sql", secret / "job.sql")
    rc = cli.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Customer Payroll" in out  # full payload keeps local detail
