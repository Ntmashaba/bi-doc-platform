"""Alter-style scripts and housekeeping procedures (real-world corpus gaps)."""

from __future__ import annotations

from sqlagentdocgen.registry import Registry
from sqlagentdocgen.script_parser import parse_calls
from sqlagentdocgen.tokenizer import lex


def _consume(src: str) -> Registry:
    reg = Registry()
    bindings = []
    calls = parse_calls(lex(src), bindings_out=bindings)
    reg.consume(calls, "t.sql", bindings=bindings)
    return reg


def test_sp_add_category_is_a_noop():
    src = """
    IF NOT EXISTS (SELECT name FROM msdb.dbo.syscategories WHERE name=N'[Uncategorized (Local)]' AND category_class=1)
    BEGIN
    EXEC @ReturnCode = msdb.dbo.sp_add_category @class=N'JOB', @type=N'LOCAL', @name=N'[Uncategorized (Local)]'
    END
    EXEC msdb.dbo.sp_add_job @job_name=N'J';
    """
    reg = _consume(src)
    assert reg.unrecognised == []
    assert "name:j" in reg.jobs


def test_sp_set_sqlagent_properties_is_a_noop():
    src = """
    EXEC msdb.dbo.sp_set_sqlagent_properties @email_save_in_sent_folder=1
    EXEC msdb.dbo.sp_add_job @job_name=N'J';
    """
    reg = _consume(src)
    assert reg.unrecognised == []


def test_delete_jobstep_all_then_readd():
    src = """
    DECLARE @p nvarchar(128) = N'Tabular process';
    EXEC msdb.dbo.sp_add_job @job_name=@p;
    EXEC msdb.dbo.sp_add_jobstep @job_name=@p, @step_id=1, @step_name=N'old';
    -- alter script style:
    SELECT @jobId = job_id FROM msdb.dbo.sysjobs_view WHERE name=@p;
    EXEC msdb.dbo.sp_delete_jobstep @job_id=@jobId, @step_id=0;
    EXEC msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_id=1, @step_name=N'new';
    """
    reg = _consume(src)
    job = reg.jobs["name:tabular process"]
    assert [s.step_name for s in job.steps] == ["new"]


def test_delete_jobstep_specific_id():
    src = """
    EXEC msdb.dbo.sp_add_job @job_name=N'J';
    EXEC msdb.dbo.sp_add_jobstep @job_name=N'J', @step_id=1, @step_name=N'a';
    EXEC msdb.dbo.sp_add_jobstep @job_name=N'J', @step_id=2, @step_name=N'b';
    EXEC msdb.dbo.sp_delete_jobstep @job_name=N'J', @step_id=1;
    """
    reg = _consume(src)
    assert [s.step_name for s in reg.jobs["name:j"].steps] == ["b"]


def test_conditional_guid_delete_leaves_no_stub():
    src = """
    IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs_view WHERE name = N'J')
        EXEC msdb.dbo.sp_delete_job @job_id=N'c2944343-1111-2222-3333-444444444444';
    EXEC msdb.dbo.sp_add_job @job_name=N'J';
    """
    reg = _consume(src)
    assert list(reg.jobs) == ["name:j"]
    assert not any(not j.created for j in reg.jobs.values())


def test_add_jobschedule_with_literal_schedule_uid():
    src = """
    EXEC msdb.dbo.sp_add_job @job_name=N'J';
    EXEC msdb.dbo.sp_add_jobschedule @job_name=N'J', @name=N'sched',
        @enabled=1, @freq_type=4, @freq_interval=1,
        @freq_subday_type=4, @freq_subday_interval=5,
        @schedule_uid=N'c469be3f-0000-4000-8000-0000000000ab';
    """
    reg = _consume(src)
    uids = [k for k in reg.schedules if k.startswith("uid:")]
    assert uids == ["uid:c469be3f-0000-4000-8000-0000000000ab"]
    sched = reg.schedules[uids[0]]
    assert sched.uid == "c469be3f-0000-4000-8000-0000000000ab"
    job = reg.jobs["name:j"]
    assert job.schedule_keys == uids
