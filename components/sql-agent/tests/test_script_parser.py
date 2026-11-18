"""Script parser tests: EXEC extraction, parameters, conditionals."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlagentdocgen.script_parser import parse_calls
from sqlagentdocgen.tokenizer import lex


def calls(src):
    return parse_calls(lex(src))


def test_named_parameters():
    c = calls("EXEC msdb.dbo.sp_add_job @job_name = N'A', @enabled = 1")[0]
    assert c.proc == "sp_add_job"
    assert c.full_proc == "msdb.dbo.sp_add_job"
    assert c.named["job_name"].kind == "string"
    assert c.named["job_name"].value == "A"
    assert c.named["enabled"].as_int() == 1
    assert c.recognised


def test_execute_and_rc_capture():
    c = calls("EXECUTE @ReturnCode = sp_add_job @job_name = N'A'")[0]
    assert c.proc == "sp_add_job"
    assert "job_name" in c.named


def test_positional_parameters():
    c = calls("EXEC sp_add_jobstep @jobId, N'Step 1', N'TSQL'")[0]
    # positional order: job_id, job_name, step_id, ... — the registry
    # disambiguates job_id vs job_name by value kind
    assert c.positional[0].kind == "var"
    assert c.positional[1].value == "Step 1"


def test_output_capture_named():
    c = calls("EXEC sp_add_job @job_name = N'A', @job_id = @jobId OUTPUT")[0]
    assert c.outputs == {"job_id": "@jobId"}
    assert c.named["job_id"].kind == "var"


def test_output_capture_positional():
    # job_id is positional slot 15 in sp_add_job
    args = ", ".join(["N'J'", "1", "N''", "1", "N'[Uncategorized (Local)]'",
                      "NULL", "N'sa'", "0", "0", "0", "0", "NULL", "NULL",
                      "NULL", "0", "@jobId OUTPUT"])
    c = calls(f"EXEC sp_add_job {args}")[0]
    assert c.outputs.get("job_id") == "@jobId"


def test_if_exists_makes_conditional():
    src = ("IF EXISTS (SELECT job_id FROM sysjobs WHERE name = N'J') "
           "EXEC sp_delete_job @job_name = N'J'")
    c = calls(src)[0]
    assert c.proc == "sp_delete_job"
    assert c.conditional


def test_if_goto_does_not_mark_next_exec():
    src = ("IF (@@ERROR <> 0) GOTO QuitWithRollback\n"
           "EXEC sp_add_jobstep @job_name = N'J', @step_name = N'S'")
    c = calls(src)[0]
    assert not c.conditional


def test_begin_end_block_makes_conditional():
    src = ("IF @x = 1\nBEGIN\nEXEC sp_delete_job @job_name = N'J'\nEND\n"
           "EXEC sp_add_job @job_name = N'J'")
    cs = calls(src)
    assert cs[0].conditional and cs[0].proc == "sp_delete_job"
    assert not cs[1].conditional


def test_command_string_with_fake_exec_inside():
    src = ("EXEC sp_add_jobstep @job_name = N'J', @step_name = N'S', "
           "@command = N'EXEC msdb.dbo.sp_add_jobstep @job_name = N''FAKE'''")
    cs = calls(src)
    assert len(cs) == 1
    assert "FAKE" in cs[0].named["command"].value


def test_proc_name_in_comment_ignored():
    cs = calls("-- EXEC sp_add_job @job_name = N'FAKE'\nEXEC sp_add_job @job_name = N'R'")
    assert len(cs) == 1
    assert cs[0].named["job_name"].value == "R"


def test_unrecognised_proc_flagged():
    c = calls("EXEC dbo.sp_my_custom_proc @x = 1")[0]
    assert not c.recognised
    assert c.proc == "sp_my_custom_proc"


def test_trailing_semicolon_terminates_call():
    cs = calls("EXEC sp_add_job @job_name = N'A';\nEXEC sp_add_job @job_name = N'B';")
    assert [c.named["job_name"].value for c in cs] == ["A", "B"]


def test_missing_semicolon_still_splits_calls():
    cs = calls("EXEC sp_add_job @job_name = N'A'\nEXEC sp_add_job @job_name = N'B'")
    assert len(cs) == 2


def test_dotted_and_bracketed_proc_name():
    c = calls("EXEC [msdb].[dbo].[sp_add_job] @job_name = N'A'")[0]
    assert c.proc == "sp_add_job"


def test_negative_number_value():
    c = calls("EXEC sp_add_jobstep @job_name = N'J', @step_id = -1")[0]
    assert c.named["step_id"].as_int() == -1
