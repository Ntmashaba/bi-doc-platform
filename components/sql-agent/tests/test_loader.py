"""Loader tests: sniffing and folder loading."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlagentdocgen.loader import is_job_script_head, load_path


def test_sniff_positive_case_insensitive():
    assert is_job_script_head(b"exec SP_ADD_JOB @job_name=N'x'")
    assert is_job_script_head("EXEC sp_add_job".encode("utf-8"))


def test_sniff_negative():
    assert not is_job_script_head(b"SELECT 1 FROM somewhere")
    assert not is_job_script_head(b"")


def test_sniff_bom_tolerated():
    assert is_job_script_head(b"\xef\xbb\xbf-- sp_add_job")


def test_sniff_utf16_ssms_output():
    text = "EXEC msdb.dbo.sp_add_job @job_name=N'J'"
    assert is_job_script_head(b"\xff\xfe" + text.encode("utf-16-le"))
    assert is_job_script_head(b"\xfe\xff" + text.encode("utf-16-be"))
    # no BOM: NUL-byte heuristic
    assert is_job_script_head(text.encode("utf-16-le"))


def test_load_folder_skips_non_job_sql(tmp_path):
    (tmp_path / "job.sql").write_text("EXEC sp_add_job @job_name=N'J'", encoding="utf-8")
    (tmp_path / "query.sql").write_text("SELECT * FROM t", encoding="utf-8")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "job2.sql").write_text("-- sp_add_jobstep only\nEXEC sp_add_jobstep 1",
                                  encoding="utf-8")
    result = load_path(tmp_path)
    assert len(result.files) == 2
    assert len(result.skipped) == 1
    assert result.skipped[0].endswith("query.sql")


def test_load_single_file(tmp_path):
    p = tmp_path / "job.sql"
    p.write_text("EXEC sp_add_job @job_name=N'J'", encoding="utf-8")
    assert len(load_path(p).files) == 1
    q = tmp_path / "q.sql"
    q.write_text("SELECT 1", encoding="utf-8")
    assert load_path(q).skipped == [str(q)]


def test_missing_path_reports_error(tmp_path):
    result = load_path(tmp_path / "nope")
    assert result.errors
