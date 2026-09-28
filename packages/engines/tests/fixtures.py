"""Seeded inputs shared by the adapter tests (handoff A29)."""
import json
from pathlib import Path

PBI_MARKERS = {
    "odbc_password": "SEEDPWD_Odbc_7731",
    "sql_literal": "SEEDSSN_900-11-2222",
    "entered_row": "SEEDROW_Alice_Smith",
    "entered_base64": "i45WSlTSUTJUitWJVjICM41hzFgA",
    "web_token": "SEEDTOKEN_sig_abc123",
}
M = {
    "Creds": f'let S = Odbc.DataSource("Driver={{SQL Server}};Server=db1;Uid=svc;Pwd={PBI_MARKERS["odbc_password"]}") in S',
    "Native": 'let S = Sql.Database("finance-sql.corp.local","FinanceDW",[Query="SELECT * FROM dbo.People '
              f"WHERE ssn = '{PBI_MARKERS['sql_literal']}'\"]) in S",
    "Entered": f'let S = Table.FromRows({{{{"{PBI_MARKERS["entered_row"]}", "555-0100"}}}}, {{"Name","Phone"}}) in S',
    "Compressed": 'let S = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText('
                  f'"{PBI_MARKERS["entered_base64"]}", BinaryEncoding.Base64), Compression.Deflate))) in S',
    "Api": f'let S = Json.Document(Web.Contents("https://api.contoso.com/data?token={PBI_MARKERS["web_token"]}")) in S',
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
               "sql_literal": "SEEDSSN_900-33-4444", "precopy_literal": "SEEDPRE_2024"}


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
    ]}}],
}


def adf_factory(folder: Path) -> Path:
    for kind, items in ADF_FACTORY.items():
        (folder / kind).mkdir(parents=True, exist_ok=True)
        for it in items:
            (folder / kind / f"{it['name']}.json").write_text(json.dumps(it), encoding="utf-8")
    return folder
