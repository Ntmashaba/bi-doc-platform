"""XMLA Synchronize as SSMS scripts it: source under Source/Object, source server in a connection string.

Synthetic commands only; the password below is a marker that must never appear in any output.
"""

from __future__ import annotations

from pathlib import Path

from sqlagentdocgen import cli
from sqlagentdocgen.analyzer import analyse
from sqlagentdocgen.loader import load_path
from sqlagentdocgen.xmla import analyse_xmla

NS = 'xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"'
MARKER = "Pw-Never-Shown-7f3a"
REAL = (f"<Synchronize {NS}><Source><ConnectionString>Provider=MSOLAP.8;Data Source=SRC-SSAS\\TABULAR;"
        f"Integrated Security=SSPI;User ID=svc;Password={MARKER};Initial Catalog=SalesModel</ConnectionString>"
        f"<Object><DatabaseID>SalesModel</DatabaseID></Object></Source>"
        f"<SynchronizeSecurity>SkipMembership</SynchronizeSecurity><ApplyCompression>true</ApplyCompression>"
        f"</Synchronize>")


def test_the_scripted_layout_names_target_source_and_source_server():
    op = analyse_xmla(REAL).operations[0]
    assert (op.kind, op.object_path, op.reads, op.detail) == (
        "synchronize", "SalesModel", ["SalesModel"], {"sourceServer": "SRC-SSAS\\TABULAR"})


def test_the_direct_layout_still_reads():
    op = analyse_xmla(f"<Synchronize {NS}><Object><DatabaseID>Copy</DatabaseID></Object>"
                      f"<Source><DatabaseID>Original</DatabaseID></Source></Synchronize>").operations[0]
    assert (op.object_path, op.reads, op.detail) == ("Copy", ["Original"], {})


def test_entities_put_the_source_on_its_own_server(tmp_path):
    (tmp_path / "sync.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name = N'Sync'\n"
        "EXEC msdb.dbo.sp_add_jobstep @job_name = N'Sync', @step_name = N'sync', @subsystem = N'ANALYSISCOMMAND',\n"
        f"    @server = N'QUERY-SSAS', @command = N'{REAL}'\n", encoding="utf-8")
    p = analyse(load_path(tmp_path))
    entities = {e["key"]: (e["kind"], e["endpoint"], [u["operation"] for u in e["usage"]]) for e in p["entities"]}
    assert entities == {
        "ssas/query-ssas/salesmodel": ("ssas_database", {"system": "ssas", "server": "QUERY-SSAS",
                                                         "database": "SalesModel"}, ["synchronize"]),
        "ssas/src-ssas\\tabular/salesmodel": ("ssas_database", {"system": "ssas", "server": "SRC-SSAS\\TABULAR",
                                                                "database": "SalesModel"}, ["read"]),
    }
    assert p["coverage"]["unknownOperations"] == 0


def test_no_part_of_the_connection_string_but_the_server_reaches_any_output(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "sync.sql").write_text(
        "EXEC msdb.dbo.sp_add_job @job_name = N'Sync'\n"
        "EXEC msdb.dbo.sp_add_jobstep @job_name = N'Sync', @step_name = N'sync', @subsystem = N'ANALYSISCOMMAND',\n"
        f"    @server = N'QUERY-SSAS', @command = N'{REAL}'\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    assert cli.main([str(src), "--html", str(out / "doc.html"), "--also", "json,word,csv,agent"]) == 0
    for path in out.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            if path.suffix == ".docx":
                import zipfile
                data = b"".join(zipfile.ZipFile(path).read(n) for n in zipfile.ZipFile(path).namelist())
            assert MARKER.encode() not in data, path.name


def test_both_layouts_are_known_operations(tmp_path):
    direct = (f"<Synchronize {NS}><Object><DatabaseID>Copy</DatabaseID></Object>"
              f"<Source><DatabaseID>Original</DatabaseID></Source></Synchronize>")
    for i, command in enumerate((REAL, direct)):
        folder = tmp_path / str(i)
        folder.mkdir()
        (folder / "sync.sql").write_text(
            "EXEC msdb.dbo.sp_add_job @job_name = N'Sync'\n"
            "EXEC msdb.dbo.sp_add_jobstep @job_name = N'Sync', @step_name = N'sync', @subsystem = N'ANALYSISCOMMAND',\n"
            f"    @command = N'{command}'\n", encoding="utf-8")
        assert analyse(load_path(folder))["coverage"]["unknownOperations"] == 0
