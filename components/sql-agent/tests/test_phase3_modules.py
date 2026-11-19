"""Phase 3 acceptance tests: execution graph, processing view, normalized
model (entities), redaction, and the processing-related review findings.

Standalone: synthetic scripts only (v4 §10).
"""

from __future__ import annotations

import pytest

from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path
from sqlagentdocgen.redact import REDACTED, scrub_payload, scrub_text


def _payload(tmp_path, text, name="jobs.sql"):
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return analyse(load_path(f))


def _job(payload, name):
    for j in payload["jobs"]:
        if j["name"] == name:
            return j
    raise AssertionError(f"job {name!r} not in payload")


XMLA_FULL = ("<Batch xmlns=\"http://schemas.microsoft.com/analysisservices/2003/engine\">"
             "<Process><Type>ProcessFull</Type><Object><DatabaseID>DW</DatabaseID>"
             "<CubeID>Sales</CubeID></Object></Process></Batch>")

TMSL_PARTITION = ('{"refresh": {"type": "add", "objects": [{"database": "DW", '
                  '"table": "Fact", "partition": "Fact-2026"}]}}')


def _ssas_job(name, step_name, command, *, retry=None, server=None,
              on_success=None, on_fail=None, step_id=None, subsys="ANALYSISCOMMAND"):
    args = [f"@step_name = N'{step_name}'", f"@subsystem = N'{subsys}'",
            f"@command = N'{command}'"]
    if step_id is not None:
        args.append(f"@step_id = {step_id}")
    if server:
        args.append(f"@server = N'{server}'")
    if retry is not None:
        args.append(f"@retry_attempts = {retry}")
    if on_success is not None:
        args.append(f"@on_success_action = {on_success[0]}")
        if len(on_success) > 1:
            args.append(f"@on_success_step_id = {on_success[1]}")
    if on_fail is not None:
        args.append(f"@on_fail_action = {on_fail[0]}")
        if len(on_fail) > 1:
            args.append(f"@on_fail_step_id = {on_fail[1]}")
    steps = ",\n    ".join(args)
    return (
        f"EXEC msdb.dbo.sp_add_job @job_name = N'{name}', @job_id = @jobId OUTPUT\n"
        f"EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId,\n    {steps}\n"
    )


# ==========================================================================
# execution graph
# ==========================================================================

FLOW_SCRIPT = """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'Flow', @job_id = @jobId OUTPUT, @start_step_id = 1
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 1, @step_name = N'one',
    @command = N'SELECT 1', @on_success_action = 3, @on_fail_action = 4, @on_fail_step_id = 3
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 2, @step_name = N'two',
    @command = N'SELECT 1', @on_success_action = 1, @on_fail_action = 2
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 3, @step_name = N'handler',
    @command = N'SELECT 1', @on_success_action = 4, @on_success_step_id = 1, @on_fail_action = 2
"""


def test_graph_goto_and_quit_actions(tmp_path):
    g = _payload(tmp_path, FLOW_SCRIPT)["executionGraphs"]["name:flow"]
    by_from = {}
    for e in g["edges"]:
        by_from.setdefault((e["from"], e["on"]), e)
    assert by_from[("1", "success")]["to"] == "2"           # goto next
    assert by_from[("1", "failure")]["to"] == "3"           # goto step 3
    assert by_from[("2", "success")]["to"] == "quit-success"
    assert by_from[("2", "failure")]["to"] == "quit-failure"
    assert by_from[("3", "success")]["to"] == "1"           # loop back
    assert all(not e["defaulted"] for e in g["edges"])      # all scripted


def test_graph_start_step_and_reachability(tmp_path):
    g = _payload(tmp_path, FLOW_SCRIPT)["executionGraphs"]["name:flow"]
    assert g["start"] == "1"
    assert g["unreachable"] == []
    assert g["loops"] and all(len(loop) >= 2 for loop in g["loops"])
    assert any("1" in loop for loop in g["loops"])


def test_graph_defaults_marked_and_last_step_next_ends_success(tmp_path):
    payload = _payload(tmp_path, """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'Defaults', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 1, @step_name = N'a', @command = N'SELECT 1'
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 2, @step_name = N'b', @command = N'SELECT 2',
    @on_success_action = 3
""")
    g = payload["executionGraphs"]["name:defaults"]
    a_succ = next(e for e in g["edges"] if e["from"] != "2" and e["on"] == "success")
    assert a_succ["action"] == "quitSuccess" and a_succ["defaulted"] is True
    # "go to next step" on the last step ends the job reporting success
    b_succ = next(e for e in g["edges"] if e["from"] == "2" and e["on"] == "success")
    assert b_succ["to"] == "quit-success" and b_succ["action"] == "gotoNextStepEnd"


def test_graph_unreachable_step_reported(tmp_path):
    payload = _payload(tmp_path, """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'Gapped', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 1, @step_name = N'a',
    @command = N'SELECT 1', @on_success_action = 1
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 2, @step_name = N'stranded',
    @command = N'SELECT 2'
""")
    g = payload["executionGraphs"]["name:gapped"]
    assert [u["name"] for u in g["unreachable"]] == ["stranded"]


def test_graph_unresolved_goto_flagged(tmp_path):
    payload = _payload(tmp_path, """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'Broken', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 1, @step_name = N'a',
    @command = N'SELECT 1', @on_fail_action = 4, @on_fail_step_id = 99
""")
    g = payload["executionGraphs"]["name:broken"]
    edge = next(e for e in g["edges"] if e["on"] == "failure")
    assert edge["unresolved"] is True and edge["gotoStepId"] == 99


# ==========================================================================
# processing view
# ==========================================================================

def _two_ssas_jobs(tmp_path, *, same_time=True, shared=False):
    """Two jobs processing the same database; schedules coincide or not."""
    first_time = "220000"
    second_time = "220000" if same_time else "230000"
    script = f"""
DECLARE @jobId BINARY(16)
DECLARE @jobId2 BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'Proc A', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_name = N'full',
    @subsystem = N'ANALYSISCOMMAND', @server = N'SSAS1', @command = N'{XMLA_FULL}'
EXEC msdb.dbo.sp_add_job @job_name = N'Proc B', @job_id = @jobId2 OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId2, @step_name = N'add',
    @subsystem = N'ANALYSISCOMMAND', @server = N'SSAS1', @command = N'{TMSL_PARTITION}'
"""
    if shared:
        script += """
DECLARE @sid INT
EXEC msdb.dbo.sp_add_schedule @schedule_name = N'Shared', @enabled = 1,
    @freq_type = 4, @freq_interval = 1, @freq_subday_type = 1,
    @active_start_time = 220000, @schedule_id = @sid OUTPUT
EXEC msdb.dbo.sp_attach_schedule @job_id = @jobId, @schedule_id = @sid
EXEC msdb.dbo.sp_attach_schedule @job_id = @jobId2, @schedule_id = @sid
"""
    else:
        script += f"""
EXEC msdb.dbo.sp_add_jobschedule @job_id = @jobId, @name = N'nightly A',
    @freq_type = 4, @freq_interval = 1, @freq_subday_type = 1, @active_start_time = {first_time}
EXEC msdb.dbo.sp_add_jobschedule @job_id = @jobId2, @name = N'nightly B',
    @freq_type = 4, @freq_interval = 1, @freq_subday_type = 1, @active_start_time = {second_time}
"""
    return _payload(tmp_path, script)


def test_processing_targets_group_by_database(tmp_path):
    payload = _two_ssas_jobs(tmp_path)
    targets = payload["processing"]["targets"]
    assert len(targets) == 1
    t = targets[0]
    assert (t["server"], t["database"]) == ("SSAS1", "DW")
    assert t["operationCount"] == 2
    assert t["jobs"] == ["Proc A", "Proc B"]


def test_processing_coincidence_same_start_time(tmp_path):
    coinc = _two_ssas_jobs(tmp_path, same_time=True)["processing"]["coincidence"]
    assert len(coinc) == 1
    assert coinc[0]["jobs"] == ["Proc A", "Proc B"]
    assert coinc[0]["database"] == "DW"


def test_processing_no_coincidence_different_times(tmp_path):
    assert _two_ssas_jobs(tmp_path, same_time=False)["processing"]["coincidence"] == []


def test_processing_coincidence_via_shared_schedule(tmp_path):
    coinc = _two_ssas_jobs(tmp_path, shared=True)["processing"]["coincidence"]
    assert len(coinc) == 1


def test_processing_partition_granularity_flag(tmp_path):
    ops = _two_ssas_jobs(tmp_path)["processing"]["operations"]
    by_kind = {(o["kind"], o["verb"]): o for o in ops}
    assert by_kind[("process", "ProcessFull")]["partitionGranular"] is False
    assert by_kind[("refresh", "add")]["partitionGranular"] is True


# ==========================================================================
# normalized model (entities)
# ==========================================================================

def test_entities_ssas_database_and_object(tmp_path):
    payload = _two_ssas_jobs(tmp_path)
    by_key = {e["key"]: e for e in payload["entities"]}
    db = by_key["ssas/ssas1/dw"]
    assert db["kind"] == "ssas_database"
    assert db["endpoint"] == {"system": "ssas", "server": "SSAS1", "database": "DW"}
    # the cube-level ProcessFull and the partition refresh both count as usage
    assert len(db["usage"]) == 2
    obj = by_key["ssas/ssas1/dw/sales"]
    assert obj["kind"] == "ssas_object"
    assert obj["endpoint"]["object"] == "Sales"


def test_entities_tsql_and_proc(tmp_path):
    payload = _payload(tmp_path, """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'ETL', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_name = N'load',
    @database_name = N'Warehouse',
    @command = N'INSERT INTO dbo.Fact SELECT * FROM staging.Raw; EXEC dbo.usp_Audit;'
""")
    by_kind = {}
    for e in payload["entities"]:
        by_kind.setdefault(e["kind"], []).append(e)
    tables = {e["endpoint"]["object"]: e for e in by_kind["table"]}
    assert tables["Fact"]["usage"][0]["operation"] == "write"
    assert tables["Raw"]["usage"][0]["operation"] == "read"
    assert tables["Fact"]["endpoint"]["database"] == "Warehouse"
    proc = by_kind["stored_procedure"][0]
    assert proc["endpoint"]["object"] == "usp_Audit"
    assert proc["usage"][0]["operation"] == "execute"


def test_entities_ssis_catalog_and_file_paths(tmp_path):
    payload = _payload(tmp_path, """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'SSIS', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_name = N'catalog pkg',
    @subsystem = N'SSIS',
    @command = N'/ISSERVER "\\"\\SSISDB\\DW\\Load\\LoadFact.dtsx\\"" /SERVER SQL1'
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_name = N'file pkg',
    @subsystem = N'SSIS',
    @command = N'DTEXEC /FILE "C:\\packages\\archive.dtsx"'
""")
    kinds = {(e["kind"], e["endpoint"]["system"]): e for e in payload["entities"]}
    assert ("ssis_package", "ssis") in kinds
    assert kinds[("ssis_package", "ssis")]["endpoint"]["path"].endswith("LoadFact.dtsx")
    assert ("ssis_package", "file") in kinds
    assert kinds[("ssis_package", "file")]["endpoint"]["path"].lower().endswith("archive.dtsx")


# ==========================================================================
# redaction
# ==========================================================================

def test_scrub_text_connection_string_and_sqlcmd():
    text, n = scrub_text("Provider=SQLNCLI11;Data Source=SSAS1;Password=hunter2;")
    assert "hunter2" not in text and REDACTED in text and n == 1
    text, n = scrub_text('sqlcmd -S SQL1 -U sa -P "s3cret value" -Q "SELECT 1"')
    assert "s3cret value" not in text and n == 1
    text, n = scrub_text("https://user:pw@storage.example/c?sig=abc123")
    assert "pw" not in text and "abc123" not in text and n == 2


def test_payload_scrub_redacts_command_secret_but_keeps_owner(tmp_path):
    payload = _payload(tmp_path, f"""
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'Secrets', @job_id = @jobId OUTPUT,
    @owner_login_name = N'EXAMPLE\\svc'
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_name = N'cmd',
    @subsystem = N'CmdExec',
    @command = N'sqlcmd -S SQL1 -U sa -P hunter2 -Q "SELECT 1"'
""")
    clean, count = scrub_payload(payload)
    assert count == 1
    assert "hunter2" not in str(clean)
    assert clean["jobs"][0]["owner"] == "EXAMPLE\\svc"  # owner is config, not secret


# ==========================================================================
# processing-related findings (v4 §6)
# ==========================================================================

def test_no_retry_finding_fires_on_ssas_process_step(tmp_path):
    payload = _payload(tmp_path,
        "DECLARE @jobId BINARY(16)\n"
        + _ssas_job("Nightly", "full", XMLA_FULL, retry=None))
    codes = [i["code"] for i in payload["issues"]]
    assert "step-no-retry" in codes
    issue = next(i for i in payload["issues"] if i["code"] == "step-no-retry")
    assert issue["severity"] == "warning"
    assert "retryAttempts = 0" in issue["message"]


def test_no_retry_finding_silent_when_retry_scripted(tmp_path):
    payload = _payload(tmp_path,
        "DECLARE @jobId BINARY(16)\n"
        + _ssas_job("Nightly", "full", XMLA_FULL, retry=3))
    assert "step-no-retry" not in [i["code"] for i in payload["issues"]]


def test_no_retry_finding_ignores_tsql_and_non_process_ops(tmp_path):
    payload = _payload(tmp_path, """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'Mixed', @job_id = @jobId OUTPUT
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_name = N'tsql', @command = N'SELECT 1'
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_name = N'backup',
    @subsystem = N'ANALYSISCOMMAND',
    @command = N'<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"><Backup><Object><DatabaseID>DW</DatabaseID></Object><File>dw.abf</File></Backup></Batch>'
""")
    assert "step-no-retry" not in [i["code"] for i in payload["issues"]]


def test_process_full_granularity_set_level(tmp_path):
    # ProcessFull at cube scope and nothing at partition granularity anywhere
    payload = _payload(tmp_path,
        "DECLARE @jobId BINARY(16)\n"
        + _ssas_job("Nightly", "full", XMLA_FULL, retry=1))
    issue = next((i for i in payload["issues"] if i["code"] == "process-full-granularity"), None)
    assert issue is not None and issue["severity"] == "info"
    assert "no job in this script set processes at partition granularity" in issue["message"]


def test_process_full_granularity_silent_when_partition_seen(tmp_path):
    payload = _two_ssas_jobs(tmp_path)  # Proc B refreshes at partition granularity
    assert "process-full-granularity" not in [i["code"] for i in payload["issues"]]
