"""Batch-scoped variable tracking: DECLARE/SET/SELECT-assign.

These patterns come from real-world deployment scripts (parameterised job
names, SET-concatenated XMLA commands, alter-style scripts that look up
job_id from sysjobs by name).
"""

from __future__ import annotations

from sqlagentdocgen.registry import Registry
from sqlagentdocgen.script_parser import parse_calls
from sqlagentdocgen.tokenizer import lex


def _calls(src: str):
    bindings = []
    calls = parse_calls(lex(src), bindings_out=bindings)
    return calls, bindings


def test_declare_inline_literal_resolves_job_name():
    src = """
    DECLARE @p_jobName nvarchar(128) = N'Tabular process';
    EXEC msdb.dbo.sp_add_job @job_name=@p_jobName, @enabled=1;
    """
    calls, _ = _calls(src)
    reg = Registry()
    reg.consume(calls, "t.sql")
    assert list(reg.jobs) == ["name:tabular process"]
    assert reg.jobs["name:tabular process"].name == "Tabular process"


def test_declare_multi_var_with_expression_default():
    src = """
    DECLARE @a int = (SELECT CONVERT(int, 8)),
            @b nvarchar(36) = NEWID(),
            @c nvarchar(10) = N'kept';
    EXEC msdb.dbo.sp_add_job @job_name=@c, @enabled=@a;
    """
    calls, _ = _calls(src)
    reg = Registry()
    reg.consume(calls, "t.sql")
    # expression defaults are unknown, plain literals resolve
    assert list(reg.jobs) == ["name:kept"]


def test_set_literal_and_null():
    src = """
    DECLARE @v nvarchar(128);
    SET @v = N'first';
    EXEC msdb.dbo.sp_add_job @job_name=@v;
    SET @v = NULL;
    EXEC msdb.dbo.sp_add_jobstep @job_id=@v, @step_id=1, @step_name=N's';
    """
    calls, _ = _calls(src)
    assert calls[0].named["job_name"].resolved == "first"
    # known-NULL stays unresolved (resolved is None)
    assert calls[1].named["job_id"].resolved is None


def test_set_concat_builds_command():
    src = """
    DECLARE @cmd nvarchar(max);
    SET @cmd = N'';
    SET @cmd = @cmd + N'<Batch ';
    SET @cmd = @cmd + N'xmlns="x">';
    SET @cmd = @cmd + N'</Batch>';
    EXEC msdb.dbo.sp_add_jobstep @job_name=N'j', @step_id=1,
        @step_name=N's', @subsystem=N'ANALYSISCOMMAND', @command=@cmd;
    """
    calls, _ = _calls(src)
    arg = calls[0].named["command"]
    assert arg.resolved == '<Batch xmlns="x"></Batch>'


def test_set_concat_after_null_stays_null():
    src = """
    DECLARE @cmd nvarchar(10);
    SET @cmd = NULL;
    SET @cmd = @cmd + N'x';
    EXEC msdb.dbo.sp_add_job @job_name=N'j', @description=@cmd;
    """
    calls, _ = _calls(src)
    assert calls[0].named["description"].resolved is None


def test_set_expression_marks_unknown():
    src = """
    DECLARE @v nvarchar(128) = N'lost';
    SET @v = SOME_FUNCTION(1);
    EXEC msdb.dbo.sp_add_job @job_name=@v;
    """
    calls, _ = _calls(src)
    assert calls[0].named["job_name"].resolved is None


def test_select_job_id_binding_by_literal_name():
    src = """
    SELECT @jobId = job_id FROM msdb.dbo.sysjobs WHERE name = N'My Job';
    EXEC msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_id=1, @step_name=N's';
    """
    calls, bindings = _calls(src)
    assert bindings == [(bindings[0][0], "@jobid", "My Job")]
    reg = Registry()
    reg.consume(calls, "t.sql", bindings=bindings)
    job = reg.jobs["name:my job"]
    assert [s.step_name for s in job.steps] == ["s"]


def test_select_job_id_binding_with_variable_name():
    src = """
    DECLARE @p nvarchar(128) = N'My Job';
    SELECT @jobId = job_id FROM msdb.dbo.sysjobs_view WHERE name = @p;
    EXEC msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_id=1, @step_name=N's';
    """
    calls, bindings = _calls(src)
    assert bindings and bindings[0][2] == "My Job"
    reg = Registry()
    reg.consume(calls, "t.sql", bindings=bindings)
    assert "name:my job" in reg.jobs


def test_binding_orders_before_later_uses():
    # The binding must apply before the EXEC that uses the variable, even
    # though both arrive in the same consume() batch.
    src = """
    EXEC msdb.dbo.sp_add_job @job_name=N'A';
    SELECT @jobId = job_id FROM msdb.dbo.sysjobs WHERE name = N'A';
    EXEC msdb.dbo.sp_update_job @job_id=@jobId, @enabled=0;
    """
    calls, bindings = _calls(src)
    reg = Registry()
    reg.consume(calls, "t.sql", bindings=bindings)
    assert reg.jobs["name:a"].enabled == 0
    assert not reg.warnings


def test_select_non_sysjobs_is_not_a_binding():
    src = """
    SELECT @jobId = job_id FROM dbo.some_other_table WHERE name = N'X';
    EXEC msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_id=1, @step_name=N's';
    """
    calls, bindings = _calls(src)
    assert bindings == []
