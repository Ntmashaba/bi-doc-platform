"""Regression tests for the Phase 2 review findings.

Covers: UTF-16 SSMS scripts, rename-followed-by-name-reference, success-only
notification levels, and TMSL createOrReplace/alter target paths.
"""

from __future__ import annotations

import codecs

from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import is_job_script_head, load_path
from sqlagentdocgen.registry import Registry
from sqlagentdocgen.script_parser import parse_calls
from sqlagentdocgen.tokenizer import lex
from sqlagentdocgen.tmsl import analyse_tmsl


# -- UTF-16 loader ---------------------------------------------------------


def test_sniff_utf16_with_bom():
    text = "EXEC sp_add_job @job_name=N'J'"
    assert is_job_script_head(codecs.BOM_UTF16_LE + text.encode("utf-16-le"))
    assert is_job_script_head(codecs.BOM_UTF16_BE + text.encode("utf-16-be"))


def test_sniff_utf16_without_bom():
    # SSMS "Unicode" saved without a BOM: NUL-interleaved ASCII
    text = "-- SSMS job script\nEXEC sp_add_job @job_name=N'J'\n"
    assert is_job_script_head(text.encode("utf-16-le"))


def test_load_utf16_file_decodes_text(tmp_path):
    p = tmp_path / "job.sql"
    p.write_bytes(
        codecs.BOM_UTF16_LE
        + "EXEC msdb.dbo.sp_add_job @job_name=N'J16'".encode("utf-16-le")
    )
    result = load_path(p)
    assert len(result.files) == 1
    assert "sp_add_job" in result.files[0].text
    payload = analyse(result)
    assert [j["name"] for j in payload["jobs"]] == ["J16"]


# -- widened sniff needles (sp_add_schedule / sp_attach_schedule) ----------


def test_sniff_schedule_needles():
    assert is_job_script_head(b"EXEC sp_add_schedule @schedule_name=N'S'")
    assert is_job_script_head(b"EXEC sp_attach_schedule @schedule_name=N'S'")


# -- rename keeps the name index consistent ---------------------------------


def _consume(src: str) -> Registry:
    reg = Registry()
    bindings = []
    calls = parse_calls(lex(src), bindings_out=bindings)
    reg.consume(calls, "t.sql", bindings=bindings)
    return reg


def test_rename_updates_name_index():
    src = """
    EXEC msdb.dbo.sp_add_job @job_name=N'Old';
    EXEC msdb.dbo.sp_update_job @job_name=N'Old', @new_name=N'New';
    EXEC msdb.dbo.sp_add_jobstep @job_name=N'New', @step_name=N'S';
    """
    reg = _consume(src)
    assert len(reg.jobs) == 1  # no dangling "New" stub
    job = next(iter(reg.jobs.values()))
    assert job.name == "New"
    assert [s.step_name for s in job.steps] == ["S"]
    assert reg.job_by_name.get("old") is None
    assert reg.job_by_name["new"] == job.key


def test_rename_absorbs_earlier_stub():
    # a reference to the future name before the rename creates a stub; the
    # rename must absorb it rather than split the job
    src = """
    EXEC msdb.dbo.sp_add_job @job_name=N'Old';
    EXEC msdb.dbo.sp_add_jobstep @job_name=N'New', @step_name=N'early';
    EXEC msdb.dbo.sp_update_job @job_name=N'Old', @new_name=N'New';
    EXEC msdb.dbo.sp_add_jobstep @job_name=N'New', @step_name=N'late';
    """
    reg = _consume(src)
    assert len(reg.jobs) == 1
    job = next(iter(reg.jobs.values()))
    assert [s.step_name for s in job.steps] == ["early", "late"]


# -- success-only notification does not suppress the failure warning --------


def test_notify_on_success_still_warns(tmp_path):
    (tmp_path / "j.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name=N'J', @notify_level_email=1;\n",
        encoding="utf-8")
    payload = analyse(load_path(tmp_path))
    codes = {i["code"] for i in payload["issues"]}
    assert "job-failure-unnotified" in codes


def test_notify_on_failure_suppresses_warning(tmp_path):
    (tmp_path / "j.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name=N'J', @notify_level_email=2;\n",
        encoding="utf-8")
    payload = analyse(load_path(tmp_path))
    codes = {i["code"] for i in payload["issues"]}
    assert "job-failure-unnotified" not in codes


def test_notify_on_completion_suppresses_warning(tmp_path):
    (tmp_path / "j.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name=N'J', @notify_level_page=3;\n",
        encoding="utf-8")
    payload = analyse(load_path(tmp_path))
    codes = {i["code"] for i in payload["issues"]}
    assert "job-failure-unnotified" not in codes


# -- TMSL createOrReplace / alter target paths ------------------------------


def test_tmsl_create_or_replace_reads_nested_object():
    script = analyse_tmsl(
        '{"createOrReplace": {"object": {"database": "Sales", "table": "Fact"},'
        ' "table": {"name": "Fact", "columns": []}}}'
    )
    assert script.ok
    assert script.operations[0].object_path == "Sales/Fact"


def test_tmsl_alter_reads_nested_object():
    script = analyse_tmsl(
        '{"alter": {"object": {"database": "Sales", "table": "Fact",'
        ' "partition": "P1"}, "table": {"name": "Fact"}}}'
    )
    assert script.ok
    assert script.operations[0].object_path == "Sales/Fact/P1"


def test_tmsl_definition_dict_is_not_a_path_component():
    # regression: the table definition body must never be stringified into
    # the object path
    script = analyse_tmsl(
        '{"createOrReplace": {"object": {"database": "D"},'
        ' "table": {"name": "T"}}}'
    )
    assert script.operations[0].object_path == "D"
