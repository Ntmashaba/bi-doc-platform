"""Seeded inputs shared by the adapter tests (handoff A29)."""
import json
from pathlib import Path

PBI_MARKERS = {
    "odbc_password": "SEEDPWD_Odbc_7731",
    "sql_literal": "SEEDSSN_900-11-2222",
    "entered_row": "SEEDROW_Alice_Smith",
    "entered_base64": "i45WSlTSUTJUitWJVjICM41hzFgA",
    "web_token": "SEEDTOKEN_sig_abc123",
    "url_password": "SEEDPWD_Url_5521",
    "bearer_token": "SEEDBEARER_9f8e7d6c5b4a",
    "api_key": "SEEDAPIKEY_3c2b1a",
    "piped_literal": "SEEDPIPE_Beta",
    "step_name": "SEEDSTEP_Payroll_Joined",
    "described_column": "SEEDCOL_Salary_Band",
}
M = {
    "Creds": f'let S = Odbc.DataSource("Driver={{SQL Server}};Server=db1;Uid=svc;Pwd={PBI_MARKERS["odbc_password"]}") in S',
    "Native": 'let S = Sql.Database("finance-sql.corp.local","FinanceDW",[Query="SELECT * FROM dbo.People '
              f"WHERE ssn = '{PBI_MARKERS['sql_literal']}'\"]) in S",
    "Entered": f'let S = Table.FromRows({{{{"{PBI_MARKERS["entered_row"]}", "555-0100"}}}}, {{"Name","Phone"}}) in S',
    "Compressed": 'let S = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText('
                  f'"{PBI_MARKERS["entered_base64"]}", BinaryEncoding.Base64), Compression.Deflate))) in S',
    "Api": f'let S = Json.Document(Web.Contents("https://api.contoso.com/data?token={PBI_MARKERS["web_token"]}")) in S',
    # user:password@host in a URL, and credentials sent as request headers
    "Userinfo": f'let S = Json.Document(Web.Contents("https://svc_reader:{PBI_MARKERS["url_password"]}@api.contoso.com/v1/orders")) in S',
    "Headers": 'let S = Json.Document(Web.Contents("https://api.contoso.com/v1/stock", [Headers=['
               f'Authorization="Bearer {PBI_MARKERS["bearer_token"]}", #"x-api-key"="{PBI_MARKERS["api_key"]}"]])) in S',
    # " | " inside a literal: withholding must not stop at it
    "Piped": 'let Source = Sql.Database("finance-sql.corp.local,1444", "FinanceDW"),\n'
             f'    F = Table.SelectRows(Source, each [Status] = "A | {PBI_MARKERS["piped_literal"]}")\nin\n    F',
    # a step name is part of the code: withheld with it, and with every list of steps read from it
    "Stepped": 'let Source = Sql.Database("finance-sql.corp.local,1444", "FinanceDW"),\n'
               f'    #"{PBI_MARKERS["step_name"]}" = Table.RemoveColumns(Source, {{"{PBI_MARKERS["described_column"]}"}})\n'
               f'in\n    #"{PBI_MARKERS["step_name"]}"',
    "Sales": 'let Source = Sql.Database("finance-sql.corp.local,1444", "FinanceDW"),\n'
             '    T = Source{[Schema="dbo",Item="FactSales"]}[Data]\nin\n    T',
}
DAX_TABLE = "CALENDAR(DATE(2020, 1, 1), DATE(2020, 12, 31))"
MEASURE_TAG = "1b2c3d4e-0000-4000-8000-000000000002"
SALES_TAG = "1b2c3d4e-0000-4000-8000-000000000001"


def pbi_model(folder: Path) -> Path:
    tables = [{"name": n, "columns": [{"name": "A"}], "partitions": [{"name": n, "source": {"type": "m", "expression": e}}]}
              for n, e in M.items() if n != "Sales"]
    tables.append({"name": "Sales", "lineageTag": SALES_TAG, "columns": [{"name": "Amount"}],
                   "measures": [{"name": "Revenue", "expression": "SUM(Sales[Amount])", "lineageTag": MEASURE_TAG,
                                 "description": "Select a value from the slicer to filter."}],
                   "partitions": [{"name": "Sales", "source": {"type": "m", "expression": M["Sales"]}}]})
    tables.append({"name": "Dates", "columns": [{"name": "Date"}],
                   "partitions": [{"name": "Dates", "source": {"type": "calculated", "expression": DAX_TABLE}}]})
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "model.bim"
    path.write_text(json.dumps({"model": {"name": "Probe", "tables": tables}}), encoding="utf-8")
    return path


ADF_MARKERS = {"inline_password": "SEEDPWD_Inline_4410", "sas_signature": "SEEDSIG_sv2022abc",
               "sql_literal": "SEEDSSN_900-33-4444", "precopy_literal": "SEEDPRE_2024",
               "url_password": "SEEDPWD_AdfUrl_8802", "bearer_token": "SEEDBEARER_Adf_1a2b3c4d",
               "script_api_key": "SEEDAPIKEY_Adf_77c1"}


def _ls(name, typ, tp):
    return {"name": name, "properties": {"type": typ, "typeProperties": tp}}


def _ds(name, typ, lsn, tp):
    return {"name": name, "properties": {"type": typ, "typeProperties": tp,
                                         "linkedServiceName": {"referenceName": lsn, "type": "LinkedServiceReference"}}}


def _ref(n):
    return {"referenceName": n, "type": "DatasetReference"}


ADF_FACTORY = {
    "linkedService": [
        _ls("LS_Sql", "AzureSqlDatabase", {"connectionString":
            f"Server=tcp:sql1.corp.local,1433;Database=DW;User ID=svc;Password={ADF_MARKERS['inline_password']};"}),
        _ls("LS_Blob", "AzureBlobStorage", {"sasUri": f"https://acct.blob.core.windows.net/?sv=2022&sig={ADF_MARKERS['sas_signature']}"}),
        _ls("LS_Lake", "AzureBlobFS", {"url": "https://lake.dfs.core.windows.net"}),
    ],
    "dataset": [
        _ds("DS_Sales", "AzureSqlTable", "LS_Sql", {"schema": "dbo", "table": "Sales"}),
        _ds("DS_Daily", "Parquet", "LS_Lake", {"location": {"type": "AzureBlobFSLocation", "fileSystem": "raw",
                                                             "folderPath": "sales/daily"}}),
    ],
    "pipeline": [{"name": "PL_Load", "properties": {"description": "Loads daily sales.", "activities": [
        {"name": "Copy daily", "type": "Copy", "inputs": [_ref("DS_Daily")], "outputs": [_ref("DS_Sales")],
         "typeProperties": {"source": {"type": "ParquetSource"}, "sink": {
             "type": "AzureSqlSink", "preCopyScript": f"DELETE FROM dbo.Sales WHERE batch = '{ADF_MARKERS['precopy_literal']}'"}}},
        {"name": "Lookup literal", "type": "Lookup", "typeProperties": {"dataset": _ref("DS_Sales"), "source": {
            "type": "AzureSqlSource", "sqlReaderQuery": f"SELECT MAX(id) AS m FROM dbo.People WHERE ssn = '{ADF_MARKERS['sql_literal']}'"}}},
        {"name": "Purge", "type": "Delete", "typeProperties": {"dataset": _ref("DS_Daily")}},
        {"name": "Notify", "type": "WebActivity", "typeProperties": {
            "url": f"https://svc_notify:{ADF_MARKERS['url_password']}@hooks.contoso.com/v1/loaded", "method": "POST",
            "headers": {"Authorization": f"Bearer {ADF_MARKERS['bearer_token']}"}, "body": {"status": "loaded"}}},
        {"name": "Refresh cache", "type": "Script", "linkedServiceName": {"referenceName": "LS_Sql", "type": "LinkedServiceReference"},
         "typeProperties": {"scripts": [{"type": "NonQuery", "text":
             "EXEC sys.sp_invoke_external_rest_endpoint @url = N'https://cache.contoso.com/refresh', "
             f"@headers = N'{{\"x-api-key\": \"{ADF_MARKERS['script_api_key']}\"}}'"}]}},
    ]}}],
}


def adf_factory(folder: Path) -> Path:
    for kind, items in ADF_FACTORY.items():
        (folder / kind).mkdir(parents=True, exist_ok=True)
        for it in items:
            (folder / kind / f"{it['name']}.json").write_text(json.dumps(it), encoding="utf-8")
    return folder


SQL_AGENT_SCRIPT = """\
-- Synthetic test fixture (handoff A29): one SSAS processing job, scripted the way
-- SSMS "Script Job as > CREATE To" writes it. It names no real system.
USE [msdb]
GO
EXEC msdb.dbo.sp_add_job
    @job_name = N'Seeded Nightly Processing',
    @enabled = 1,
    @description = N'Full process of the seeded sales cube.',
    @owner_login_name = N'sa'
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Seeded Nightly Processing',
    @step_id = 1,
    @step_name = N'Process the sales cube',
    @subsystem = N'ANALYSISCOMMAND',
    @server = N'SEEDED-SSAS-01',
    @command = N'<Batch Transaction="true" xmlns="http://schemas.microsoft.com/analysisservices/2003/engine">
  <Process>
    <Type>ProcessFull</Type>
    <Object>
      <DatabaseID>SeededDW</DatabaseID>
      <CubeID>Sales</CubeID>
    </Object>
  </Process>
</Batch>'
EXEC msdb.dbo.sp_add_jobschedule
    @job_name = N'Seeded Nightly Processing',
    @name = N'Nightly 23:00',
    @enabled = 1,
    @freq_type = 4,
    @freq_interval = 1,
    @freq_subday_type = 1,
    @active_start_time = 230000
EXEC msdb.dbo.sp_add_jobserver @job_name = N'Seeded Nightly Processing'
GO
"""


def sql_agent_jobs(path: Path) -> Path:
    """One scripted SQL Agent job at `path` (an SSMS "Script Job as > CREATE To"-shaped file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SQL_AGENT_SCRIPT, encoding="utf-8")
    return path


SQL_AGENT_MIXED_SCRIPT = r"""\
-- Synthetic projection/admission fixture (v4 5.3): one job touching every step
-- subsystem, with a marker token in each command that shared projection must
-- never keep. It names no real system.
USE [msdb]
GO
EXEC msdb.dbo.sp_add_job
    @job_name = N'Seeded Mixed Subsystems',
    @enabled = 1,
    @description = N'One step per subsystem, for projection tests.',
    @owner_login_name = N'sa'
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Seeded Mixed Subsystems',
    @step_id = 1,
    @step_name = N'Process the cube',
    @subsystem = N'ANALYSISCOMMAND',
    @server = N'SEEDED-SSAS-01',
    @command = N'<Batch Transaction="true" data-seeded="MARKER-XMLA-a11ce" xmlns="http://schemas.microsoft.com/analysisservices/2003/engine">
  <Process>
    <Type>ProcessFull</Type>
    <Object>
      <DatabaseID>SeededDW</DatabaseID>
      <CubeID>Sales</CubeID>
    </Object>
  </Process>
</Batch>'
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Seeded Mixed Subsystems',
    @step_id = 2,
    @step_name = N'Refresh the table',
    @subsystem = N'ANALYSISQUERY',
    @server = N'SEEDED-SSAS-01',
    @command = N'{"refresh": {"type": "full", "note": {"text": "MARKER-TMSL-b22df"}, "objects": [{"database": "SeededDW", "table": "Sales"}]}}'
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Seeded Mixed Subsystems',
    @step_id = 3,
    @step_name = N'Run the loader',
    @subsystem = N'TSQL',
    @command = N'EXEC dbo.usp_SeededLoad -- MARKER-TSQL-c33e0'
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Seeded Mixed Subsystems',
    @step_id = 4,
    @step_name = N'Shell out',
    @subsystem = N'PowerShell',
    @command = N'Write-Output ''MARKER-POSH-d44f1'''
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Seeded Mixed Subsystems',
    @step_id = 5,
    @step_name = N'Echo a marker',
    @subsystem = N'CmdExec',
    @command = N'cmd /c echo MARKER-CMDEXEC-e55f2'
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Seeded Mixed Subsystems',
    @step_id = 6,
    @step_name = N'Run the package',
    @subsystem = N'SSIS',
    @command = N'dtexec /FILE "D:\SeededETL\Nightly.dtsx"'
EXEC msdb.dbo.sp_add_jobserver @job_name = N'Seeded Mixed Subsystems'
GO
"""

# Tokens that exist only inside raw step commands (or, for ssis_path, in the extracted
# file-package path surfaces): none of them may survive shared projection.
SQL_AGENT_MARKERS = {
    "xmla": "MARKER-XMLA-a11ce",
    "tmsl": "MARKER-TMSL-b22df",
    "tsql": "MARKER-TSQL-c33e0",
    "powershell": "MARKER-POSH-d44f1",
    "cmdexec": "MARKER-CMDEXEC-e55f2",
    "ssis_path": "SeededETL",
}


def sql_agent_mixed(path: Path) -> Path:
    """One scripted job with a step per subsystem at `path` (projection and admission tests)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SQL_AGENT_MIXED_SCRIPT, encoding="utf-8")
    return path
