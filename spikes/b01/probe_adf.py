"""B01 probes (ADF): seeded secrets, endpoint identity (SQL port, path case) and
delete-as-writer. Builds a throwaway factory, runs the pinned CLI, inspects output."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import _engines

SECRETS = {
    "inline_password": "SEEDPWD_Inline_4410",
    "sas_signature": "SEEDSIG_sv2022abc",
    "sql_literal": "SEEDSSN_900-33-4444",
}


def ls(name, typ, tp):
    return {"name": name, "properties": {"type": typ, "typeProperties": tp}}


def ds(name, typ, lsn, tp):
    return {"name": name, "properties": {"type": typ, "typeProperties": tp,
                                         "linkedServiceName": {"referenceName": lsn, "type": "LinkedServiceReference"}}}


def ref(n):
    return {"referenceName": n, "type": "DatasetReference"}


FACTORY = {
    "linkedService": [
        ls("LS_Sql1433", "AzureSqlDatabase", {"connectionString":
           f"Server=tcp:sql1.corp.local,1433;Database=DW;User ID=svc;Password={SECRETS['inline_password']};"}),
        ls("LS_Sql1444", "AzureSqlDatabase", {"connectionString": "Server=tcp:sql1.corp.local,1444;Database=DW;"}),
        ls("LS_Blob", "AzureBlobStorage", {"sasUri":
           f"https://acct.blob.core.windows.net/?sv=2022&sig={SECRETS['sas_signature']}"}),
        ls("LS_Lake", "AzureBlobFS", {"url": "https://lake.dfs.core.windows.net"}),
    ],
    "dataset": [
        ds("DS_A", "AzureSqlTable", "LS_Sql1433", {"schema": "dbo", "table": "Sales"}),
        ds("DS_B", "AzureSqlTable", "LS_Sql1444", {"schema": "dbo", "table": "Sales"}),
        ds("DS_Upper", "Parquet", "LS_Lake", {"location": {"type": "AzureBlobFSLocation", "fileSystem": "raw",
                                                           "folderPath": "Sales/Daily"}}),
        ds("DS_Lower", "Parquet", "LS_Lake", {"location": {"type": "AzureBlobFSLocation", "fileSystem": "raw",
                                                           "folderPath": "sales/daily"}}),
    ],
    "pipeline": [{"name": "PL_Probe", "properties": {"activities": [
        {"name": "Copy to 1433", "type": "Copy", "inputs": [ref("DS_Upper")], "outputs": [ref("DS_A")],
         "typeProperties": {"source": {"type": "ParquetSource"}, "sink": {"type": "AzureSqlSink"}}},
        {"name": "Copy to 1444", "type": "Copy", "inputs": [ref("DS_Lower")], "outputs": [ref("DS_B")],
         "typeProperties": {"source": {"type": "ParquetSource"}, "sink": {"type": "AzureSqlSink"}}},
        {"name": "Lookup literal", "type": "Lookup", "typeProperties": {"dataset": ref("DS_A"), "source": {
            "type": "AzureSqlSource", "sqlReaderQuery": f"SELECT * FROM dbo.People WHERE ssn = '{SECRETS['sql_literal']}'"}}},
        {"name": "Purge lake", "type": "Delete", "typeProperties": {"dataset": ref("DS_Lower")}},
    ]}}],
}


def main():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "factory"
        for kind, items in FACTORY.items():
            (root / kind).mkdir(parents=True)
            for it in items:
                (root / kind / f"{it['name']}.json").write_text(json.dumps(it), encoding="utf-8")
        out = Path(d) / "probe.html"
        subprocess.run([sys.executable, str(_engines.ADF / "generate_docs.py"), str(root), "-o", str(out), "--json"],
                       check=True, capture_output=True)
        html = out.read_text(encoding="utf-8")
        data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    report = {"secrets": {k: {"html": v in html, "json": v in json.dumps(data)} for k, v in SECRETS.items()}}
    ents = data["entities"]
    sql = [e for e in ents if "sales" in json.dumps(e).lower() and e.get("kind") in ("table",)]
    files = [e for e in ents if "daily" in json.dumps(e).lower()]
    report["sql_dbo_sales_entities"] = [e["key"] for e in sql]
    report["lake_sales_daily_entities"] = [e["key"] for e in files]
    report["expected"] = {"sql_dbo_sales_entities": 2, "lake_sales_daily_entities": 2}
    report["edges"] = data.get("lineageEdges", [])
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
