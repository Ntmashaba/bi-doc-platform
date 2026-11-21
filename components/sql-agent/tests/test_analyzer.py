"""End-to-end analyzer tests over the synthetic example script."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlagentdocgen.analyzer import analyse, scope_key
from sqlagentdocgen.loader import load_path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "example_jobs.sql"


def payload():
    return analyse(load_path(EXAMPLE))


def jobs_by_name(p):
    return {j["name"]: j for j in p["jobs"]}


def test_two_jobs_found():
    p = payload()
    assert set(jobs_by_name(p)) == {"SSAS Nightly Processing", "Warehouse Cleanup"}
    assert p["coverage"]["inputFiles"] == 1
    assert p["coverage"]["skippedFiles"] == []
    assert p["coverage"]["unrecognisedStatements"] == 0


def test_ssas_job_steps():
    p = payload()
    job = jobs_by_name(p)["SSAS Nightly Processing"]
    assert job["recreatePreamble"] is True
    assert job["owner"] == "sa"
    assert job["registeredOn"] == ["(local)"]
    assert len(job["steps"]) == 2

    xmla_step = job["steps"][0]
    assert xmla_step["subsystem"] == "ANALYSISCOMMAND"
    assert xmla_step["server"] == "SSAS-EXAMPLE-01"
    assert xmla_step["transaction"] is True
    assert xmla_step["retryAttempts"] == 3
    assert xmla_step["retryIntervalMinutes"] == 15
    op = xmla_step["operations"][0]
    assert op["kind"] == "process" and op["verb"] == "ProcessFull"
    assert op["object"] == "SalesDW/Sales"

    tmsl_step = job["steps"][1]
    assert tmsl_step["operations"][0]["kind"] == "refresh"
    assert tmsl_step["operations"][0]["verb"] == "add"
    assert tmsl_step["operations"][0]["object"] == "SalesTabular/FactSales/FactSales-Current"
    assert tmsl_step["proxy"] == "EXAMPLE\\svc_proxy"


def test_tsql_step_harvest():
    p = payload()
    job = jobs_by_name(p)["Warehouse Cleanup"]
    step = job["steps"][0]
    assert step["subsystem"] == "TSQL"
    assert step["database"] == "WarehouseStaging"
    written = {w.lower() for w in step["sql"]["writes"]}
    procs = {x.lower() for x in step["sql"]["procs"]}
    assert any("loadlog" in w for w in written)
    assert any("usp_purgeerrors" in pr for pr in procs)


def test_schedule_models():
    p = payload()
    job = jobs_by_name(p)["SSAS Nightly Processing"]
    sched = job["schedules"][0]
    assert sched["freqType"] == 8
    assert sched["activeStartTime"] == "22:00:00"
    # freq_interval 62 = 2|4|8|16|32 = Monday..Friday
    assert "Monday" in sched["summary"] and "Friday" in sched["summary"]
    assert "Saturday" not in sched["summary"]

    shared = p["sharedSchedules"]
    assert len(shared) == 1
    assert shared[0]["name"] == "Shared Weekly Maintenance"
    assert shared[0]["attachedJobs"] == ["Warehouse Cleanup"]
    assert "Sunday" in shared[0]["summary"]


def test_issues_wording_contract():
    p = payload()
    messages = json.dumps(p["issues"]).lower()
    assert "silent failure" not in messages
    assert "overlap" not in messages and "contention" not in messages
    codes = {i["code"] for i in p["issues"]}
    # owner 'sa' + no operator notification on the SSAS job
    assert "job-owner-sa" in codes
    assert "job-failure-unnotified" in codes
    # non-T-SQL step with proxy
    assert "step-proxy" in codes
    # ProcessFull is at cube scope here, so no process-full-database finding
    assert "process-full-database" not in codes


def test_scope_key_stable_and_selection_shaped():
    p = payload()
    key1 = p["scope"]["key"]
    assert key1.startswith("selection-")
    assert payload()["scope"]["key"] == key1
    # a third job changes the key
    from sqlagentdocgen.registry import JobDraft

    class _J(JobDraft):
        pass

    j = [JobDraft(key="name:a", name="A", created=True)]
    assert scope_key(j) != scope_key([])


def test_process_full_at_database_scope_flagged(tmp_path):
    src = tmp_path / "p.sql"
    src.write_text(
        "EXEC sp_add_job @job_name=N'P', @job_id=@j OUTPUT\n"
        "EXEC sp_add_jobstep @job_id=@j, @step_name=N'S', @subsystem=N'ANALYSISCOMMAND',"
        " @command=N'<Batch><Process><Type>ProcessFull</Type>"
        "<Object><DatabaseID>DB</DatabaseID></Object></Process></Batch>'\n",
        encoding="utf-8",
    )
    p = analyse(load_path(src))
    codes = {i["code"] for i in p["issues"]}
    assert "process-full-database" in codes


def test_unknown_operation_counted(tmp_path):
    src = tmp_path / "u.sql"
    src.write_text(
        "EXEC sp_add_job @job_name=N'U', @job_id=@j OUTPUT\n"
        "EXEC sp_add_jobstep @job_id=@j, @step_name=N'S', @subsystem=N'ANALYSISCOMMAND',"
        " @command=N'<Batch><Frobnicate/></Batch>'\n",
        encoding="utf-8",
    )
    p = analyse(load_path(src))
    assert p["coverage"]["unknownOperations"] == 1


def test_malformed_command_counted_not_fatal(tmp_path):
    src = tmp_path / "m.sql"
    src.write_text(
        "EXEC sp_add_job @job_name=N'M', @job_id=@j OUTPUT\n"
        "EXEC sp_add_jobstep @job_id=@j, @step_name=N'S', @subsystem=N'ANALYSISCOMMAND',"
        " @command=N'<Batch><Process>'\n",
        encoding="utf-8",
    )
    p = analyse(load_path(src))
    assert p["coverage"]["unknownOperations"] == 1
    assert p["jobs"][0]["steps"][0]["parseError"]
