"""Registry tests: correlation of jobs, steps, schedules, variables."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlagentdocgen.registry import Registry
from sqlagentdocgen.script_parser import parse_calls
from sqlagentdocgen.tokenizer import lex, statements


def build(*scripts):
    reg = Registry()
    for name, src in scripts:
        for batch_text, _ in statements(src):
            reg.consume(parse_calls(lex(batch_text)), name)
    return reg


BASIC = """
EXEC sp_add_job @job_name = N'Nightly', @enabled = 1,
     @owner_login_name = N'domain\\svc', @job_id = @jobId OUTPUT
EXEC sp_add_jobstep @job_id = @jobId, @step_name = N'S1',
     @subsystem = N'TSQL', @command = N'SELECT 1'
"""


def test_job_and_step_via_variable():
    reg = build(("a.sql", "DECLARE @jobId UNIQUEIDENTIFIER\n" + BASIC))
    assert len(reg.jobs) == 1
    job = next(iter(reg.jobs.values()))
    assert job.name == "Nightly"
    assert len(job.steps) == 1
    assert job.steps[0].subsystem == "TSQL"
    assert not reg.warnings


def test_step_by_job_name():
    src = """
EXEC sp_add_job @job_name = N'J'
EXEC sp_add_jobstep @job_name = N'J', @step_name = N'S1'
"""
    reg = build(("a.sql", src))
    job = next(iter(reg.jobs.values()))
    assert len(job.steps) == 1


def test_step_by_guid_literal():
    guid = "12345678-1234-1234-1234-1234567890AB"
    src = f"""
EXEC sp_add_job @job_name = N'J', @job_id = N'{guid}'
EXEC sp_add_jobstep @job_id = N'{guid}', @step_name = N'S1'
"""
    reg = build(("a.sql", src))
    job = next(iter(reg.jobs.values()))
    assert job.guid == guid.lower()
    assert job.key == "guid:" + guid.lower()
    assert len(job.steps) == 1


def test_shared_schedule_attach_by_variable():
    src = """
EXEC sp_add_job @job_name = N'J1', @job_id = @j1 OUTPUT
EXEC sp_add_job @job_name = N'J2', @job_id = @j2 OUTPUT
EXEC sp_add_schedule @schedule_name = N'Shared', @freq_type = 4,
     @schedule_id = @sid OUTPUT
EXEC sp_attach_schedule @job_id = @j1, @schedule_id = @sid
EXEC sp_attach_schedule @job_id = @j2, @schedule_id = @sid
"""
    reg = build(("a.sql", src))
    assert len(reg.schedules) == 1
    sched = next(iter(reg.schedules.values()))
    assert sched.shared
    assert len(sched.attached_jobs) == 2
    for job in reg.jobs.values():
        assert job.schedule_keys == [sched.key]


def test_inline_jobschedule():
    src = """
EXEC sp_add_job @job_name = N'J', @job_id = @j OUTPUT
EXEC sp_add_jobschedule @job_id = @j, @name = N'Daily 2am',
     @freq_type = 4, @active_start_time = 20000
"""
    reg = build(("a.sql", src))
    job = next(iter(reg.jobs.values()))
    assert len(job.schedule_keys) == 1
    sched = reg.schedules[job.schedule_keys[0]]
    assert sched.freq_type == 4
    assert sched.active_start_time == 20000
    assert not sched.shared


def test_update_job_final_enabled_wins():
    src = """
EXEC sp_add_job @job_name = N'J', @enabled = 1
EXEC sp_update_job @job_name = N'J', @enabled = 0
"""
    reg = build(("a.sql", src))
    assert next(iter(reg.jobs.values())).enabled == 0


def test_recreate_preamble_detected():
    src = """
IF EXISTS (SELECT job_id FROM sysjobs WHERE name = N'J')
EXEC sp_delete_job @job_name = N'J'
EXEC sp_add_job @job_name = N'J'
"""
    reg = build(("a.sql", src))
    job = next(iter(reg.jobs.values()))
    assert job.recreate_preamble
    assert job.created


def test_add_jobserver_registration():
    src = """
EXEC sp_add_job @job_name = N'J', @job_id = @j OUTPUT
EXEC sp_add_jobserver @job_id = @j, @server_name = N'TSX01'
EXEC sp_add_jobserver @job_id = @j, @server_name = N'TSX02'
"""
    reg = build(("a.sql", src))
    assert next(iter(reg.jobs.values())).registered_on == ["TSX01", "TSX02"]


def test_add_jobserver_default_local():
    src = """
EXEC sp_add_job @job_name = N'J', @job_id = @j OUTPUT
EXEC sp_add_jobserver @job_id = @j
"""
    reg = build(("a.sql", src))
    assert next(iter(reg.jobs.values())).registered_on == ["(local)"]


def test_unresolvable_variable_warns():
    reg = build(("a.sql", "EXEC sp_add_jobstep @job_id = @missing, @step_name = N'S'"))
    assert reg.warnings
    assert reg.unparsed_steps == 0  # step was literal-command-less, not unparsed


def test_step_with_variable_command_counts_unparsed():
    src = """
EXEC sp_add_job @job_name = N'J', @job_id = @j OUTPUT
EXEC sp_add_jobstep @job_id = @j, @step_name = N'S', @command = @cmd
"""
    reg = build(("a.sql", src))
    assert reg.unparsed_steps == 1


def test_unrecognised_calls_collected():
    reg = build(("a.sql", "EXEC dbo.sp_custom 1"))
    assert len(reg.unrecognised) == 1


def test_stub_job_filled_by_later_creation():
    src = """
EXEC sp_add_jobstep @job_name = N'J', @step_name = N'S1'
EXEC sp_add_job @job_name = N'J', @owner_login_name = N'svc'
"""
    reg = build(("a.sql", src))
    assert len(reg.jobs) == 1
    job = next(iter(reg.jobs.values()))
    assert job.created
    assert len(job.steps) == 1


def test_variables_are_case_insensitive():
    src = """
EXEC sp_add_job @job_name = N'J', @job_id = @JobId OUTPUT
EXEC sp_add_jobstep @job_id = @JOBID, @step_name = N'S1'
"""
    reg = build(("a.sql", src))
    job = next(iter(reg.jobs.values()))
    assert len(job.steps) == 1
