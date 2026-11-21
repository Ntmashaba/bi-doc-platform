"""Backup and Restore: the restored database is the target, and backup passwords never reach any output.

Synthetic commands only; the passwords are markers that must never appear in what the tool writes.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from sqlagentdocgen import cli
from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path
from sqlagentdocgen.redact import scrub_text
from sqlagentdocgen.xmla import analyse_xmla

NS = 'xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"'
COMMANDS = {
    "xmla restore": (f"<Restore {NS}><File>D:\\Backups\\SalesModel.abf</File><DatabaseName>SalesModel</DatabaseName>"
                     f"<AllowOverwrite>true</AllowOverwrite><Password>Pw-Marker-XR</Password></Restore>"),
    "xmla backup": (f"<Backup {NS}><Object><DatabaseID>SalesModel</DatabaseID></Object><File>SalesModel.abf</File>"
                    f"<Password>Pw-Marker-XB</Password></Backup>"),
    "tmsl restore": '{"restore": {"database": "SalesModel", "file": "x.abf", "password": "Pw-Marker-TR"}}',
    "tmsl backup": '{"backup": {"database": "SalesModel", "file": "x.abf", "password": "Pw-Marker-TB"}}',
}


def job_folder(tmp_path: Path, command: str, name="in") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "job.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name = N'Backup and restore'\n"
        "EXEC msdb.dbo.sp_add_jobstep @job_name = N'Backup and restore', @step_name = N'step',\n"
        f"    @subsystem = N'ANALYSISCOMMAND', @server = N'ASSERVER', @command = N'{command}'\n", encoding="utf-8")
    return folder


def test_xmla_restore_targets_its_database():
    op = analyse_xmla(COMMANDS["xmla restore"]).operations[0]
    assert (op.kind, op.object_path) == ("restore", "SalesModel")
    by_id = analyse_xmla(f"<Restore {NS}><File>x.abf</File><DatabaseName>Sales Model</DatabaseName>"
                         f"<DatabaseID>SalesModel</DatabaseID></Restore>").operations[0]
    assert (by_id.object_path, by_id.detail["database"]) == ("SalesModel", "Sales Model")


def test_restore_is_an_entity_on_the_step_server(tmp_path):
    p = analyse(load_path(job_folder(tmp_path, COMMANDS["xmla restore"])))
    assert [(e["key"], [u["operation"] for u in e["usage"]]) for e in p["entities"]] == [
        ("ssas/asserver/salesmodel", ["restore"])]


@pytest.mark.parametrize("label", sorted(COMMANDS))
def test_backup_passwords_reach_no_output(tmp_path, label):
    marker = COMMANDS[label].split("Pw-Marker-")[1][:2]
    out = tmp_path / "out"
    out.mkdir()
    assert cli.main([str(job_folder(tmp_path, COMMANDS[label])), "--html", str(out / "doc.html"),
                     "--also", "json,word,csv,agent"]) == 0
    files = [p for p in out.rglob("*") if p.is_file()]
    assert len(files) == 10
    for path in files:
        data = path.read_bytes()
        if path.suffix == ".docx":
            data = b"".join(zipfile.ZipFile(path).read(n) for n in zipfile.ZipFile(path).namelist())
        assert f"Pw-Marker-{marker}".encode() not in data, path.name


def test_redaction_patterns():
    assert scrub_text("<Password>s3cret</Password>") == ("<Password>[redacted]</Password>", 1)
    assert scrub_text('<x:password a="1">s</x:password>') == ('<x:password a="1">[redacted]</x:password>', 1)
    assert scrub_text('{"password": "s\\"3"}') == ('{"password": "[redacted]"}', 1)
    for unchanged in ("<Password></Password>", "<Password/>", "<PasswordPolicy>keep</PasswordPolicy>",
                      '"passwordHint": "keep"', "<Password>[redacted]</Password>", '"password": "[redacted]"'):
        assert scrub_text(unchanged) == (unchanged, 0), unchanged
