"""The same job created in two files, or twice in one, is never merged silently. Synthetic scripts only."""

from __future__ import annotations

from pathlib import Path

from sqlagentdocgen.analyzer import analyse, scope_key
from sqlagentdocgen.loader import load_path
from sqlagentdocgen.registry import Registry

PREAMBLE = ("IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'Nightly')\n"
            "    EXEC msdb.dbo.sp_delete_job @job_name = N'Nightly'\n")
JOB = """EXEC msdb.dbo.sp_add_job @job_name = N'Nightly', @owner_login_name = N'etl'
EXEC msdb.dbo.sp_add_jobstep @job_name = N'Nightly', @step_id = 1, @step_name = N'{step}',
    @subsystem = N'TSQL', @command = N'SELECT 1'
EXEC msdb.dbo.sp_add_jobschedule @job_name = N'Nightly', @name = N'{step} schedule', @freq_type = 4,
    @freq_interval = 1, @active_start_time = 230000
"""


def analysed(tmp_path: Path, files: dict):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return analyse(load_path(tmp_path))


def summary(payload):
    return sorted((j["identity"], [s["name"] for s in j["steps"]], [s["name"] for s in j["schedules"]],
                   j["sourceFile"]) for j in payload["jobs"])


def duplicates(payload):
    return [i for i in payload["issues"] if i["code"] == "job-duplicate"]


def test_dev_and_prod_script_outs_stay_two_jobs(tmp_path):
    p = analysed(tmp_path, {"dev.sql": JOB.format(step="dev"), "prod.sql": JOB.format(step="prod")})
    assert summary(p) == [("name:nightly", ["dev"], ["dev schedule"], "dev.sql"),
                          ("name:nightly@prod.sql", ["prod"], ["prod schedule"], "prod.sql")]
    found = duplicates(p)
    assert len(found) == 1 and found[0]["severity"] == "warning"
    assert "'dev.sql' and 'prod.sql'" in found[0]["message"]


def test_drop_and_create_preambles_do_not_turn_prod_into_a_replacement_of_dev(tmp_path):
    p = analysed(tmp_path, {"dev.sql": PREAMBLE + JOB.format(step="dev"),
                            "prod.sql": PREAMBLE + JOB.format(step="prod")})
    assert [(i, steps) for i, steps, _, _ in summary(p)] == [("name:nightly", ["dev"]),
                                                            ("name:nightly@prod.sql", ["prod"])]
    assert all(j["recreatePreamble"] for j in p["jobs"])          # each file's preamble belongs to its own job
    assert len(duplicates(p)) == 1


def test_created_twice_in_one_file(tmp_path):
    p = analysed(tmp_path, {"jobs.sql": JOB.format(step="first") + JOB.format(step="second")})
    assert [(i, steps) for i, steps, _, _ in summary(p)] == [("name:nightly", ["first"]),
                                                            ("name:nightly@jobs.sql", ["second"])]
    assert "'jobs.sql' twice" in duplicates(p)[0]["message"]


def test_delete_then_create_in_one_file_is_a_replacement(tmp_path):
    script = (JOB.format(step="v1") + "EXEC msdb.dbo.sp_delete_job @job_name = N'Nightly'\n" + JOB.format(step="v2"))
    p = analysed(tmp_path, {"jobs.sql": script})
    assert summary(p) == [("name:nightly", ["v2"], ["v2 schedule"], "jobs.sql")]
    assert not duplicates(p)
    assert p["jobs"][0]["recreatePreamble"] is True


def test_a_single_job_keeps_its_identity_and_scope(tmp_path):
    p = analysed(tmp_path, {"dev.sql": JOB.format(step="dev")})
    assert [j["identity"] for j in p["jobs"]] == ["name:nightly"]
    reg = Registry()
    from sqlagentdocgen.script_parser import parse_calls
    from sqlagentdocgen.tokenizer import lex
    reg.consume(parse_calls(lex(JOB.format(step="dev"))), "dev.sql")
    # identical to the hash of "name:nightly", as before duplicates were told apart
    import hashlib
    assert scope_key(list(reg.jobs.values())) == "selection-" + hashlib.sha256(b"name:nightly").hexdigest()[:16]


def test_steps_added_from_another_file_still_extend_the_job(tmp_path):
    # alter-style: a second file adds a step without creating the job again; that is one job, no warning
    more = ("EXEC msdb.dbo.sp_add_jobstep @job_name = N'Nightly', @step_id = 2, @step_name = N'extra',\n"
            "    @subsystem = N'TSQL', @command = N'SELECT 2'\n")
    p = analysed(tmp_path, {"a.sql": JOB.format(step="dev"), "b.sql": more})
    assert [(i, steps) for i, steps, _, _ in summary(p)] == [("name:nightly", ["dev", "extra"])]
    assert not duplicates(p)


# -- three-part and four-part names (kept here with the other review regressions) ---------------------------------

def test_split_sql_name_forms():
    from sqlagentdocgen.common import split_sql_name as split
    assert split("T", "master") == (None, "master", None, "T")
    assert split("dbo.T", "master") == (None, "master", "dbo", "T")
    assert split("Sales.dbo.FactSales", "master") == (None, "Sales", "dbo", "FactSales")
    assert split("SRV.Sales.dbo.T", "master") == ("SRV", "Sales", "dbo", "T")
    assert split("[a.b].Sales.dbo.T", None) == ("a.b", "Sales", "dbo", "T")
    assert split("[Sales].[dbo].[Fact]", None) == (None, "Sales", "dbo", "Fact")
    assert split("Sales..T", None) == (None, "Sales", None, "T")
    assert split("[a]]b].dbo.T", None) == (None, "a]b", "dbo", "T")


def test_cross_database_names_bind_to_their_own_database(tmp_path):
    script = ("EXEC msdb.dbo.sp_add_job @job_name = N'Load'\n"
              "EXEC msdb.dbo.sp_add_jobstep @job_name = N'Load', @step_name = N'load', @database_name = N'master',\n"
              "    @command = N'INSERT INTO Sales.dbo.FactSales SELECT * FROM dbo.Staged'\n")
    p = analysed(tmp_path, {"load.sql": script})
    entities = {e["key"]: e["endpoint"] for e in p["entities"]}
    assert entities["sql/sales/dbo.factsales"] == {"system": "sql", "database": "Sales", "schema": "dbo",
                                                   "object": "FactSales"}
    assert entities["sql/master/dbo.staged"]["database"] == "master"


def test_database_with_the_default_schema(tmp_path):
    # the shared harvester keeps the empty part of Sales..FactSales: database Sales, schema left out
    script = ("EXEC msdb.dbo.sp_add_job @job_name = N'Load'\n"
              "EXEC msdb.dbo.sp_add_jobstep @job_name = N'Load', @step_name = N'load', @database_name = N'master',\n"
              "    @command = N'INSERT INTO Sales..FactSales SELECT 1'\n")
    p = analysed(tmp_path, {"load.sql": script})
    assert p["jobs"][0]["steps"][0]["sql"]["writes"] == ["Sales..FactSales"]
    assert [e["endpoint"] for e in p["entities"]] == [
        {"system": "sql", "database": "Sales", "schema": None, "object": "FactSales"}]


def test_if_else_alternatives_in_one_file_are_one_job(tmp_path):
    # the shape of First Responder Kit's sp_AllNightLog_Setup: one sp_add_job per branch, exactly one runs
    script = ("IF @enable = 1\n    BEGIN\n    EXEC msdb.dbo.sp_add_job @job_name = N'Poll', @enabled = 1\n    END\n"
              "ELSE\n    BEGIN\n    EXEC msdb.dbo.sp_add_job @job_name = N'Poll', @enabled = 0\n    END\n")
    p = analysed(tmp_path, {"setup.sql": script})
    assert [j["identity"] for j in p["jobs"]] == ["name:poll"]
    assert not duplicates(p)


def test_conditional_creations_in_different_files_are_still_two_jobs(tmp_path):
    guarded = "IF 1 = 1\n    EXEC msdb.dbo.sp_add_job @job_name = N'Nightly'\n"
    p = analysed(tmp_path, {"dev.sql": guarded, "prod.sql": guarded})
    assert [j["identity"] for j in p["jobs"]] == ["name:nightly", "name:nightly@prod.sql"]
    assert len(duplicates(p)) == 1
