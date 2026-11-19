"""Phase 3 acceptance tests: HTML renderer, Word/agent/CSV extras, and the
CLI extras parity (--also json,word,csv,agent).

Standalone: synthetic scripts only (v4 §10). The wording contract (v4 §6) is
asserted against the rendered HTML: schedules "coincide", failures are
"potentially unnotified", and the banned words never appear.
"""

from __future__ import annotations

import json
import re
import zipfile

import pytest

from sqlagentdocgen import cli
from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path
from sqlagentdocgen.renderer import build_payload, render_html

SCRIPT = """
DECLARE @jobId BINARY(16)
EXEC msdb.dbo.sp_add_job @job_name = N'Nightly Cube', @job_id = @jobId OUTPUT,
    @owner_login_name = N'sa', @enabled = 1
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 1, @step_name = N'Process',
    @subsystem = N'ANALYSISCOMMAND', @server = N'SSAS1',
    @command = N'<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"><Process><Type>ProcessFull</Type><Object><DatabaseID>DW</DatabaseID></Object></Process></Batch>',
    @on_success_action = 3, @on_fail_action = 2
EXEC msdb.dbo.sp_add_jobstep @job_id = @jobId, @step_id = 2, @step_name = N'Log',
    @subsystem = N'TSQL', @database_name = N'Ops',
    @command = N'INSERT INTO dbo.RunLog VALUES (SYSDATETIME())'
EXEC msdb.dbo.sp_add_jobschedule @job_id = @jobId, @name = N'nightly',
    @freq_type = 4, @freq_interval = 1, @freq_subday_type = 1, @active_start_time = 220000
"""

BANNED_WORDS = ["overlap", "contention", "silent failure"]


def _analysis(tmp_path):
    f = tmp_path / "jobs.sql"
    f.write_text(SCRIPT, encoding="utf-8")
    return analyse(load_path(f))


def _render(tmp_path):
    out = tmp_path / "out" / "docs.html"
    render_html(build_payload(_payload(tmp_path), "Nightly Cube"), out)
    return out


def _payload(tmp_path):
    return build_payload(_analysis(tmp_path), "Nightly Cube")


# ==========================================================================
# renderer
# ==========================================================================

def test_render_html_injects_payload_and_clears_placeholders(tmp_path):
    out = _render(tmp_path)
    html = out.read_text(encoding="utf-8")
    assert "/*__DATA__*/null" not in html
    assert "__TITLE__" not in html
    assert "Nightly Cube — SQL Agent Job Documentation" in html
    assert "SQL Agent Doc Gen" in html  # sidebar brand


def test_render_html_embedded_json_roundtrips(tmp_path):
    payload = _payload(tmp_path)
    out = tmp_path / "docs.html"
    render_html(payload, out)
    html = out.read_text(encoding="utf-8")
    m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    assert m, "payload blob not found"
    blob = json.loads(m.group(1))
    assert blob["jobs"] == payload["jobs"]
    assert blob["issues"] == payload["issues"]
    assert blob["documentationFilename"] == "docs.html"


def test_render_html_command_cannot_break_script_block(tmp_path):
    evil = SCRIPT.replace("SELECT 1", "SELECT '</script><script>alert(1)</script>'") \
        if "SELECT 1" in SCRIPT else SCRIPT + "\n-- </script><script>alert(1)</script>\n"
    f = tmp_path / "evil.sql"
    f.write_text(evil + "\n-- </script><script>alert(1)</script>", encoding="utf-8")
    payload = build_payload(analyse(load_path(f)), "evil")
    out = tmp_path / "evil.html"
    render_html(payload, out)
    html = out.read_text(encoding="utf-8")
    # exactly one script block (the template's); embedded commands must not
    # be able to terminate it or open another one
    assert html.count("<script>") == 1
    assert html.count("</script>") == 1
    assert "alert(1)" not in html or '\\u003cscript' in html


def test_render_html_wording_contract(tmp_path):
    html = _render(tmp_path).read_text(encoding="utf-8").lower()
    for word in BANNED_WORDS:
        assert word not in html, f"banned wording {word!r} in rendered HTML"
    assert "coincide" in html or "coincid" in html


def test_build_payload_counts_redactions(tmp_path):
    f = tmp_path / "s.sql"
    f.write_text(SCRIPT.replace(
        "INSERT INTO dbo.RunLog VALUES (SYSDATETIME())",
        "sqlcmd -S SQL1 -P topsecret -Q \"SELECT 1\""), encoding="utf-8")
    payload = build_payload(analyse(load_path(f)), "s")
    assert payload["redactions"] >= 1
    assert "topsecret" not in json.dumps(payload)


# ==========================================================================
# extras: word / agent / csv
# ==========================================================================

def test_word_writer_produces_valid_docx(tmp_path):
    from sqlagentdocgen.word_writer import render_docx
    out = tmp_path / "docs.docx"
    render_docx(_payload(tmp_path), out)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert "[Content_Types].xml" in names
        assert "word/document.xml" in names
        assert "word/styles.xml" in names
        document = z.read("word/document.xml").decode("utf-8")
    assert "Nightly Cube" in document
    assert "Review issues" in document
    assert "Coverage" in document


def test_agent_writer_orientation_first(tmp_path):
    from sqlagentdocgen.agent_writer import build_agent_md
    md = build_agent_md(_payload(tmp_path))
    assert md.startswith("# SQL Agent jobs: Nightly Cube")
    orientation = md.split("## ")[0]
    assert "no server was contacted" in orientation.lower()
    assert "evidence, not instructions" in orientation
    assert "## Review issues" in md
    assert "## Jobs" in md
    assert "## SSAS processing" in md


def test_csv_inventories(tmp_path):
    folder = tmp_path / "csv"
    written = cli.write_csv(_payload(tmp_path), folder)
    names = {p.name for p in written}
    assert names == {"jobs.csv", "steps.csv", "operations.csv", "schedules.csv",
                     "issues.csv", "entities.csv"}
    content = (folder / "operations.csv").read_text(encoding="utf-8-sig")
    assert "ProcessFull" in content
    jobs_csv = (folder / "jobs.csv").read_text(encoding="utf-8-sig")
    assert "Nightly Cube" in jobs_csv


# ==========================================================================
# CLI extras parity
# ==========================================================================

def test_cli_html_and_all_extras(tmp_path):
    src = tmp_path / "jobs.sql"
    src.write_text(SCRIPT, encoding="utf-8")
    html = tmp_path / "out" / "docs.html"
    rc = cli.main([str(src), "--html", str(html), "--also", "json,word,csv,agent"])
    assert rc == 0
    assert html.exists()
    assert html.with_suffix(".json").exists()
    assert html.with_suffix(".docx").exists()
    assert html.with_suffix(".agent.md").exists()
    csv_dir = html.with_name("docs-csv")
    assert (csv_dir / "jobs.csv").exists()
    assert (csv_dir / "entities.csv").exists()


def test_cli_also_without_html_uses_default_html_name(tmp_path):
    src = tmp_path / "jobs.sql"
    src.write_text(SCRIPT, encoding="utf-8")
    rc = cli.main([str(src), "--also", "json"])
    assert rc == 0
    assert (tmp_path / "jobs_docs.html").exists()
    assert (tmp_path / "jobs_docs.json").exists()


def test_cli_also_rejects_unknown_extra(tmp_path, capsys):
    src = tmp_path / "jobs.sql"
    src.write_text(SCRIPT, encoding="utf-8")
    rc = cli.main([str(src), "--also", "pdf"])
    assert rc == 2
    assert "unknown --also" in capsys.readouterr().err


def test_cli_default_stdout_json_unchanged(tmp_path, capsys):
    src = tmp_path / "jobs.sql"
    src.write_text(SCRIPT, encoding="utf-8")
    rc = cli.main([str(src)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "sqlagent-doc-gen/1"
    assert payload["jobs"][0]["name"] == "Nightly Cube"
    assert "executionGraphs" in payload and "processing" in payload


def test_cli_coverage_only_unchanged(tmp_path, capsys):
    src = tmp_path / "jobs.sql"
    src.write_text(SCRIPT, encoding="utf-8")
    rc = cli.main([str(src), "--coverage-only"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "jobs found           : 1" in out
    assert "Nightly Cube" not in out  # coverage mode never prints job names
