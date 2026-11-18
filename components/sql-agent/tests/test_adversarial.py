"""Adversarial extras: multi-file correlation, subsystem passthrough, JSON safety."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path


def test_multi_file_schedule_correlation(tmp_path):
    # realistic cross-file case: the shared schedule is created in one job's
    # script and attached by NAME in another job's script (variables never
    # cross file boundaries in real SSMS output)
    (tmp_path / "job1.sql").write_text(
        "EXEC sp_add_job @job_name=N'J1', @job_id=@j1 OUTPUT\n"
        "EXEC sp_add_schedule @schedule_name=N'Shared', @freq_type=4,"
        " @schedule_id=@sid OUTPUT\n"
        "EXEC sp_attach_schedule @job_id=@j1, @schedule_id=@sid\n",
        encoding="utf-8")
    (tmp_path / "job2.sql").write_text(
        "EXEC sp_add_job @job_name=N'J2', @job_id=@j2 OUTPUT\n"
        "EXEC sp_attach_schedule @job_id=@j2, @schedule_name=N'Shared'\n",
        encoding="utf-8")
    p = analyse(load_path(tmp_path))
    shared = p["sharedSchedules"]
    assert len(shared) == 1
    assert sorted(shared[0]["attachedJobs"]) == ["J1", "J2"]
    for job in p["jobs"]:
        assert job["schedules"][0]["name"] == "Shared"


def test_schedules_only_file_is_skipped_but_reported(tmp_path):
    # the 64 KB sniff needle is sp_add_job (v4); a schedules-only script does
    # not match and lands in skippedFiles — visible in coverage, never silent
    (tmp_path / "schedules_only.sql").write_text(
        "EXEC sp_add_schedule @schedule_name=N'Orphan', @freq_type=4\n",
        encoding="utf-8")
    p = analyse(load_path(tmp_path))
    assert p["jobs"] == []
    assert len(p["coverage"]["skippedFiles"]) == 1


def test_cmdexec_and_ssis_passthrough(tmp_path):
    (tmp_path / "j.sql").write_text(
        "EXEC sp_add_job @job_name=N'J', @job_id=@j OUTPUT\n"
        "EXEC sp_add_jobstep @job_id=@j, @step_name=N'bash', @subsystem=N'CmdExec',"
        " @command=N'/opt/etl/run.sh --mode nightly'\n"
        "EXEC sp_add_jobstep @job_id=@j, @step_name=N'pkg', @subsystem=N'SSIS',"
        " @command=N'/ISServer \"\\SSISDB\\ETL\\Load.dtsx\"'\n",
        encoding="utf-8")
    p = analyse(load_path(tmp_path))
    steps = p["jobs"][0]["steps"]
    assert steps[0]["subsystem"] == "CMDEXEC"
    assert steps[0]["command"] == "/opt/etl/run.sh --mode nightly"
    assert steps[0]["operations"] == []
    assert steps[1]["subsystem"] == "SSIS"
    assert steps[1]["commandKind"] == "literal"
    json.dumps(p)


def test_command_with_semicolons_and_go_like_text(tmp_path):
    (tmp_path / "j.sql").write_text(
        "EXEC sp_add_job @job_name=N'J', @job_id=@j OUTPUT\n"
        "EXEC sp_add_jobstep @job_id=@j, @step_name=N'S', @subsystem=N'TSQL',"
        " @command=N'SELECT 1;\nGO\nSELECT 2;'\n",
        encoding="utf-8")
    p = analyse(load_path(tmp_path))
    # the GO-like text is inside a string: single batch, single job, one step
    assert len(p["jobs"]) == 1
    assert len(p["jobs"][0]["steps"]) == 1


def test_payload_is_json_serializable():
    example = Path(__file__).resolve().parents[1] / "examples" / "example_jobs.sql"
    p = analyse(load_path(example))
    json.dumps(p)
