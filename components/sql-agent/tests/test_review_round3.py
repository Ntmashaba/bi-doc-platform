"""Review round 3 regressions. Synthetic scripts only; each names no real system."""

from __future__ import annotations

from pathlib import Path

from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import LoadError, load_path
from sqlagentdocgen.script_parser import parse_calls
from sqlagentdocgen.tokenizer import lex


def analysed(tmp_path: Path, files: dict):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    return analyse(load_path(tmp_path))


def codes(p):
    return [i["code"] for i in p["issues"]]


# -- H2: SSMS "Script Job as > CREATE" sets the start step with sp_update_job --------------------------------------

SSMS = """USE [msdb]
GO
BEGIN TRANSACTION
DECLARE @ReturnCode INT
SELECT @ReturnCode = 0
DECLARE @jobId BINARY(16)
EXEC @ReturnCode = msdb.dbo.sp_add_job @job_name=N'Seeded Two Steps', @enabled=1, @notify_level_eventlog=0,
    @notify_level_email=0, @description=N'No description available.', @category_name=N'[Uncategorized (Local)]',
    @owner_login_name=N'etl', @job_id = @jobId OUTPUT
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback
EXEC @ReturnCode = msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_name=N'first', @step_id=1,
    @on_success_action=1, @on_fail_action=2, @subsystem=N'TSQL', @command=N'SELECT 1'
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback
EXEC @ReturnCode = msdb.dbo.sp_add_jobstep @job_id=@jobId, @step_name=N'second', @step_id=2,
    @on_success_action=3, @on_fail_action=2, @subsystem=N'TSQL', @command=N'SELECT 2'
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback
EXEC @ReturnCode = msdb.dbo.sp_update_job @job_id = @jobId, @start_step_id = 2
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback
EXEC @ReturnCode = msdb.dbo.sp_add_jobserver @job_id = @jobId, @server_name = N'(local)'
IF (@@ERROR <> 0 OR @ReturnCode <> 0) GOTO QuitWithRollback
COMMIT TRANSACTION
GOTO EndSave
QuitWithRollback:
    IF (@@TRANCOUNT > 0) ROLLBACK TRANSACTION
EndSave:
GO
"""


def test_ssms_start_step_is_applied_and_the_graph_starts_there(tmp_path):
    p = analysed(tmp_path, {"job.sql": SSMS})
    job = p["jobs"][0]
    assert job["startStepId"] == 2
    assert p["executionGraphs"][job["identity"]]["start"] == "2"


def test_sp_update_job_applies_every_setting_it_scripts(tmp_path):
    p = analysed(tmp_path, {"job.sql": "EXEC msdb.dbo.sp_add_job @job_name = N'J', @owner_login_name = N'a'\n"
                                       "EXEC msdb.dbo.sp_update_job @job_name = N'J', @description = N'later', "
                                       "@category_name = N'Cat', @notify_level_email = 2, "
                                       "@notify_email_operator_name = N'Ops'\n"})
    job = p["jobs"][0]
    assert (job["description"], job["category"], job["notifyLevelEmail"], job["notifyEmailOperator"],
            job["owner"]) == ("later", "Cat", 2, "Ops", "a")


# -- H3: files are named by their path below the selection -------------------------------------------------------

PRE = ("IF EXISTS (SELECT job_id FROM msdb.dbo.sysjobs WHERE name = N'Nightly')\n"
       "    EXEC msdb.dbo.sp_delete_job @job_name = N'Nightly'\n")
NIGHTLY = ("EXEC msdb.dbo.sp_add_job @job_name = N'Nightly'\n"
           "EXEC msdb.dbo.sp_add_jobstep @job_name = N'Nightly', @step_name = N'{s}', @command = N'SELECT 1'\n")


def test_same_file_name_in_two_folders_is_two_jobs(tmp_path):
    p = analysed(tmp_path, {"dev/jobs.sql": NIGHTLY.format(s="dev"), "prod/jobs.sql": PRE + NIGHTLY.format(s="prod")})
    assert sorted((j["identity"], j["sourceFile"], [s["name"] for s in j["steps"]]) for j in p["jobs"]) == [
        ("name:nightly", "dev/jobs.sql", ["dev"]),
        ("name:nightly@prod/jobs.sql", "prod/jobs.sql", ["prod"])]
    assert codes(p).count("job-duplicate") == 1


def test_a_single_file_keeps_its_bare_name(tmp_path):
    p = analysed(tmp_path / "x", {"jobs.sql": NIGHTLY.format(s="only")})
    assert p["jobs"][0]["sourceFile"] == "jobs.sql"
    single = analyse(load_path(tmp_path / "x" / "jobs.sql"))
    assert single["jobs"][0]["sourceFile"] == "jobs.sql"


# -- M4: an assignment under IF / ELSE is not statically known -----------------------------------------------------

def step_command(tmp_path, prelude):
    p = analysed(tmp_path, {"job.sql": prelude + "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n"
                            "EXEC msdb.dbo.sp_add_jobstep @job_name = N'J', @step_name = N's', @command = @cmd\n"})
    return p["jobs"][0]["steps"][0], p["coverage"]["unparsedSteps"]


def test_conditional_set_makes_the_command_unknown(tmp_path):
    for prelude in ("DECLARE @cmd nvarchar(max) = N'EXEC dbo.LoadDev'\n"
                    "IF @@SERVERNAME = N'PROD01' SET @cmd = N'EXEC dbo.LoadProd'\n",
                    "DECLARE @cmd nvarchar(max) = N'EXEC dbo.LoadDev'\n"
                    "IF @@SERVERNAME = N'PROD01'\nBEGIN\n  SET @cmd = N'EXEC dbo.LoadProd'\nEND\n",
                    "DECLARE @cmd nvarchar(max)\nIF 1 = 1 SET @cmd = N'EXEC a' ELSE SET @cmd = N'EXEC b'\n"):
        step, unparsed = step_command(tmp_path / str(abs(hash(prelude))), prelude)
        assert (step["commandKind"], step.get("command"), unparsed) == ("variable", None, 1), prelude


def test_unconditional_set_is_still_known(tmp_path):
    step, unparsed = step_command(tmp_path, "DECLARE @cmd nvarchar(max) = N'EXEC dbo.A'\nSET @cmd = N'EXEC dbo.B'\n")
    assert (step["commandKind"], step["command"], unparsed) == ("literal", "EXEC dbo.B", 0)


# -- M5: a job the script deletes does not exist afterwards --------------------------------------------------------

def test_a_job_deleted_at_the_end_is_not_documented_as_existing(tmp_path):
    p = analysed(tmp_path, {"decommission.sql": NIGHTLY.format(s="s") + "EXEC msdb.dbo.sp_delete_job @job_name = N'Nightly'\n"})
    assert p["jobs"] == []
    assert codes(p) == ["job-deleted"]


# -- M6: a cut-off script is reported, never fatal ----------------------------------------------------------------

def test_truncated_scripts_do_not_stop_the_others(tmp_path):
    for i, tail in enumerate(("SELECT @j = job_id FROM", "SELECT @j = job_id FROM msdb.dbo.sysjobs WHERE name =",
                              "SELECT @j = job_id FROM msdb.dbo.sysjobs WHERE")):
        p = analysed(tmp_path / str(i), {"fine.sql": "EXEC msdb.dbo.sp_add_job @job_name = N'Fine'\n",
                                         "cut.sql": "EXEC msdb.dbo.sp_add_job @job_name = N'Cut'\nDECLARE @j BINARY(16)\n"
                                                    + tail})
        assert sorted(j["name"] for j in p["jobs"]) == ["Cut", "Fine"], tail


def test_an_unexpected_parser_failure_is_a_coverage_warning(tmp_path, monkeypatch):
    import sqlagentdocgen.analyzer as analyzer

    real = analyzer.parse_calls

    def failing(toks, bindings_out=None):
        if any(t.text == "Broken" for t in toks):
            raise IndexError("simulated")
        return real(toks, bindings_out=bindings_out)
    monkeypatch.setattr(analyzer, "parse_calls", failing)
    p = analysed(tmp_path, {"a.sql": "EXEC msdb.dbo.sp_add_job @job_name = N'Fine'\n",
                            "b.sql": "EXEC msdb.dbo.sp_add_job @job_name = N'J'\nSELECT Broken\n"})
    assert [j["name"] for j in p["jobs"]] == ["Fine"]
    assert "b.sql: could not be analysed completely" in p["warnings"]
    assert p["coverage"]["loadErrors"] == 1


# -- M7: load errors in the payload are the public form ------------------------------------------------------------

def test_load_errors_in_the_payload_carry_no_path(tmp_path):
    folder = tmp_path / "Customer Payroll"
    folder.mkdir()
    (folder / "ok.sql").write_text("EXEC msdb.dbo.sp_add_job @job_name = N'J'\n", encoding="utf-8")
    load = load_path(folder)
    load.errors.append(LoadError(detail=f"{folder / 'locked.sql'}: [Errno 13] Permission denied",
                                 public="locked.sql: could not be read"))
    load.skipped.append(str(folder / "sub" / "notes.sql"))
    p = analyse(load)
    text = repr(p)
    assert str(tmp_path) not in text and "Errno" not in text
    assert "locked.sql: could not be read" in p["warnings"]
    assert p["coverage"]["skippedFiles"] == ["sub/notes.sql"]


# -- Low ----------------------------------------------------------------------------------------------------------

def test_attaching_a_schedule_twice_lists_the_job_once(tmp_path):
    p = analysed(tmp_path, {"job.sql": "DECLARE @s INT\nEXEC msdb.dbo.sp_add_job @job_name = N'J'\n"
                            "EXEC msdb.dbo.sp_add_schedule @schedule_name = N'S', @freq_type = 4, @freq_interval = 1, "
                            "@schedule_id = @s OUTPUT\n"
                            "EXEC msdb.dbo.sp_attach_schedule @job_name = N'J', @schedule_id = @s\n"
                            "EXEC msdb.dbo.sp_attach_schedule @job_name = N'J', @schedule_id = @s\n"
                            "EXEC msdb.dbo.sp_add_jobschedule @job_name = N'J', @name = N'Local', @freq_type = 4\n"
                            "EXEC msdb.dbo.sp_add_jobschedule @job_name = N'J', @name = N'Local', @freq_type = 4\n"})
    assert p["sharedSchedules"][0]["attachedJobs"] == ["J"]
    assert [s["name"] for s in p["jobs"][0]["schedules"]] == ["S", "Local"]


def test_case_else_end_is_not_control_flow():
    after = parse_calls(lex("DECLARE @x INT\nSELECT @x = CASE WHEN 1 = 1 THEN 1 ELSE 0 END\n"
                            "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n"))
    assert after[-1].conditional is False
    inside = parse_calls(lex("IF 1 = 1\nBEGIN\n  SELECT @x = CASE WHEN 1 = 1 THEN 1 END\n"
                             "  EXEC msdb.dbo.sp_add_job @job_name = N'J'\nEND\n"
                             "EXEC msdb.dbo.sp_add_job @job_name = N'K'\n"))
    assert [c.conditional for c in inside] == [True, False]
    nested = parse_calls(lex("SELECT @x = CASE WHEN a = 1 THEN CASE WHEN b = 1 THEN 1 ELSE 2 END ELSE 3 END\n"
                             "EXEC msdb.dbo.sp_add_job @job_name = N'J'\n"))
    assert nested[-1].conditional is False


def test_go_n_does_not_duplicate_a_job_against_itself(tmp_path):
    p = analysed(tmp_path, {"job.sql": "EXEC msdb.dbo.sp_add_job @job_name = N'J'\nGO 3\n"})
    assert [j["identity"] for j in p["jobs"]] == ["name:j"]
    assert "job-duplicate" not in codes(p)


def test_deleting_a_step_renumbers_the_rest_and_their_jumps(tmp_path):
    steps = "".join(
        f"EXEC msdb.dbo.sp_add_jobstep @job_name = N'J', @step_id = {i}, @step_name = N's{i}', @command = N'SELECT {i}'"
        f", @on_success_action = {a}, @on_success_step_id = {g}\n"
        for i, a, g in ((1, 4, 3), (2, 4, 1), (3, 1, 0), (4, 4, 2)))
    p = analysed(tmp_path, {"job.sql": "EXEC msdb.dbo.sp_add_job @job_name = N'J', @start_step_id = 3\n" + steps
                            + "EXEC msdb.dbo.sp_delete_jobstep @job_name = N'J', @step_id = 2\n"})
    job = p["jobs"][0]
    assert [(s["id"], s["name"], s["onSuccessAction"], s["onSuccessStepId"]) for s in job["steps"]] == [
        (1, "s1", 4, 2), (2, "s3", 1, 0), (3, "s4", 1, None)]           # s4 jumped to the deleted step: now a quit
    assert job["startStepId"] == 2
    assert any("jumped to step 2, which the script deletes" in w for w in p["warnings"])
