"""Job scheduling-state findings (v4 §6): unscheduled, disabled-schedule and disabled jobs.

Synthetic scripts only; each names no real system.
"""

from __future__ import annotations

from pathlib import Path

from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path

JOB = """\
EXEC msdb.dbo.sp_add_job @job_name = N'{job}', @enabled = {enabled}, @owner_login_name = N'etl'
EXEC msdb.dbo.sp_add_jobstep @job_name = N'{job}', @step_id = 1, @step_name = N'only',
    @subsystem = N'TSQL', @command = N'SELECT 1'
EXEC msdb.dbo.sp_add_jobserver @job_name = N'{job}'
"""


def job(name: str, enabled: int = 1) -> str:
    return JOB.format(job=name, enabled=enabled)


def job_schedule(job_name: str, sched: str, enabled=None) -> str:
    flag = "" if enabled is None else f", @enabled = {enabled}"
    return (f"EXEC msdb.dbo.sp_add_jobschedule @job_name = N'{job_name}', @name = N'{sched}'{flag},\n"
            f"    @freq_type = 4, @freq_interval = 1, @active_start_time = 230000\n")


def findings(tmp_path: Path, script: str, code: str):
    path = tmp_path / "jobs.sql"
    path.write_text(script, encoding="utf-8")
    return [i for i in analyse(load_path(path))["issues"] if i["code"] == code]


def test_all_schedules_disabled_is_reported_once_per_job(tmp_path):
    script = (job("Nightly") + job_schedule("Nightly", "Weeknights", 0)
              + job_schedule("Nightly", "Weekend", 0))
    found = findings(tmp_path, script, "job-schedules-disabled")
    assert len(found) == 1
    assert found[0]["severity"] == "info"
    assert found[0]["job"] == "Nightly"
    assert "'Weeknights'" in found[0]["message"] and "'Weekend'" in found[0]["message"]
    assert not findings(tmp_path, script, "job-unscheduled")


def test_one_enabled_schedule_is_not_a_disabled_schedule_job(tmp_path):
    script = job("Nightly") + job_schedule("Nightly", "Old", 0) + job_schedule("Nightly", "Current", 1)
    assert not findings(tmp_path, script, "job-schedules-disabled")


def test_unscripted_enabled_is_the_msdb_default(tmp_path):
    script = job("Nightly") + job_schedule("Nightly", "Weeknights")
    assert not findings(tmp_path, script, "job-schedules-disabled")


def test_disabled_shared_schedule_attached_by_variable(tmp_path):
    script = job("Nightly") + """\
DECLARE @schedId INT
EXEC msdb.dbo.sp_add_schedule @schedule_name = N'Shared nightly', @enabled = 0,
    @freq_type = 4, @freq_interval = 1, @active_start_time = 230000, @schedule_id = @schedId OUTPUT
EXEC msdb.dbo.sp_attach_schedule @job_name = N'Nightly', @schedule_id = @schedId
"""
    found = findings(tmp_path, script, "job-schedules-disabled")
    assert [f["job"] for f in found] == ["Nightly"]
    assert "'Shared nightly'" in found[0]["message"]


def test_unscheduled_and_disabled_jobs(tmp_path):
    script = job("Manual") + job("Retired", enabled=0) + job_schedule("Retired", "Nightly")
    unscheduled = findings(tmp_path, script, "job-unscheduled")
    disabled = findings(tmp_path, script, "job-disabled")
    assert [f["job"] for f in unscheduled] == ["Manual"]
    assert [f["job"] for f in disabled] == ["Retired"]
    assert "final scripted state" in disabled[0]["message"]


def test_disabled_after_creation_uses_the_final_state(tmp_path):
    script = job("Nightly") + job_schedule("Nightly", "Weeknights") + \
        "EXEC msdb.dbo.sp_update_job @job_name = N'Nightly', @enabled = 0\n"
    assert [f["job"] for f in findings(tmp_path, script, "job-disabled")] == ["Nightly"]


# -- schedules-coincide is about jobs that process the same model -----------------------------------------------

PROCESS = """\
EXEC msdb.dbo.sp_add_jobstep @job_name = N'{job}', @step_id = 2, @step_name = N'process',
    @subsystem = N'ANALYSISCOMMAND', @server = N'SEEDED-SSAS-01',
    @command = N'<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"><Process>
<Type>ProcessFull</Type><Object><DatabaseID>{db}</DatabaseID></Object></Process></Batch>'
"""


def test_unrelated_jobs_at_the_same_time_are_not_a_finding(tmp_path):
    # the reviewer's reproduction: two T-SQL-only jobs, both nightly at 23:00
    script = (job("Nightly A") + job_schedule("Nightly A", "A 23:00")
              + job("Nightly B") + job_schedule("Nightly B", "B 23:00"))
    assert not findings(tmp_path, script, "schedules-coincide")


def test_jobs_processing_the_same_model_at_the_same_time(tmp_path):
    script = (job("Sales A") + PROCESS.format(job="Sales A", db="SeededDW") + job_schedule("Sales A", "A 23:00")
              + job("Sales B") + PROCESS.format(job="Sales B", db="SeededDW") + job_schedule("Sales B", "B 23:00"))
    found = findings(tmp_path, script, "schedules-coincide")
    assert len(found) == 1
    message = found[0]["message"]
    assert "'Sales A'" in message and "'Sales B'" in message and "SeededDW" in message
    assert "coincide" in message
    for word in ("overlap", "contention", "concurrent", "simultaneous"):
        assert word not in message.lower()


def test_jobs_processing_different_models_at_the_same_time(tmp_path):
    script = (job("Sales") + PROCESS.format(job="Sales", db="SeededDW") + job_schedule("Sales", "A 23:00")
              + job("Finance") + PROCESS.format(job="Finance", db="SeededFinance") + job_schedule("Finance", "B 23:00"))
    assert not findings(tmp_path, script, "schedules-coincide")
