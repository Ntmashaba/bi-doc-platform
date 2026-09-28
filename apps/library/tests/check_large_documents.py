"""A41: measure import, memory, search and relationship rebuild with large documents.

    python apps/library/tests/check_large_documents.py [--scale N] [--json OUT]

Builds representative large inputs (scale 1: a Power BI model with 150 tables, 3,000
columns and 1,500 measures; a Data Factory with 400 pipelines of 5 activities over 400
SQL tables, whose writes link to the model), publishes them into one library with several
smaller documents, and records:

- generate and publish time and the artifact size for each large document;
- peak Python memory during publish, rebuild and search (tracemalloc);
- full derived rebuild (search index and relationship generation) time and link count;
- search index size, whether browsers would get server search, and per-query time;
- that server search (/search) returns exactly what the browser search returns.

Numbers go to stdout (and --json); the gates only fail on broken behaviour or on a budget
far outside what a pilot library needs (see docs/performance.md).
"""
import json
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from starlette.testclient import TestClient

from bidoc_engines.generate import GenerateRequest, generate
from bidoc_library.api import create_app
from bidoc_library.config import Settings
from bidoc_library.search import search

BUDGET = {"publish_seconds": 60, "rebuild_seconds": 120, "peak_mib": 1500, "search_ms": 500}
QUERIES = ("revenue", "fact sales 17", "copy load", "dim customer", "pl_load_0042", "measure 1234", "zzz-none")


def large_model(folder: Path, scale: int) -> Path:
    tables = []
    for t in range(150 * scale):
        name = f"Fact Sales {t}" if t % 3 == 0 else f"Dim Customer {t}"
        tables.append({
            "name": name, "lineageTag": f"00000000-0000-4000-8000-{t:012d}",
            "description": f"Table {t} loaded from the warehouse for reporting.",
            "columns": [{"name": f"Column {c}", "dataType": "string", "description": f"Attribute {c} of {name}."}
                        for c in range(20)],
            "measures": [{"name": f"Measure {t * 10 + m}", "lineageTag": f"00000000-0000-4000-9000-{t * 10 + m:012d}",
                          "expression": f"CALCULATE(SUM('{name}'[Column {m}]), ALL('{name}'))",
                          "description": f"Revenue view {m} for {name}."} for m in range(10)],
            "partitions": [{"name": name, "source": {"type": "m", "expression":
                            f'let Source = Sql.Database("sql1.corp.local", "DW"),\n'
                            f'    T = Source{{[Schema="dbo",Item="T{t:04d}"]}}[Data]\nin\n    T'}}]})
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.bim").write_text(json.dumps({"model": {"name": "Enterprise model", "tables": tables}}),
                                      encoding="utf-8")
    return folder / "model.bim"


def large_factory(folder: Path, scale: int) -> Path:
    def write(kind, item):
        (folder / kind).mkdir(parents=True, exist_ok=True)
        (folder / kind / f"{item['name']}.json").write_text(json.dumps(item), encoding="utf-8")

    ref = lambda n: {"referenceName": n, "type": "DatasetReference"}  # noqa: E731
    write("linkedService", {"name": "LS_Sql", "properties": {"type": "AzureSqlDatabase", "typeProperties": {
        "connectionString": "Server=tcp:sql1.corp.local,1433;Database=DW;"}}})
    write("linkedService", {"name": "LS_Lake", "properties": {"type": "AzureBlobFS", "typeProperties": {
        "url": "https://lake.dfs.core.windows.net"}}})
    for p in range(400 * scale):
        table = f"T{p % (150 * scale):04d}"
        write("dataset", {"name": f"DS_{p:04d}", "properties": {"type": "AzureSqlTable", "typeProperties": {
            "schema": "dbo", "table": table}, "linkedServiceName": {"referenceName": "LS_Sql",
                                                                    "type": "LinkedServiceReference"}}})
        write("dataset", {"name": f"DS_Raw_{p:04d}", "properties": {"type": "Parquet", "typeProperties": {
            "location": {"type": "AzureBlobFSLocation", "fileSystem": "raw", "folderPath": f"area/{p}"}},
            "linkedServiceName": {"referenceName": "LS_Lake", "type": "LinkedServiceReference"}}})
        write("pipeline", {"name": f"PL_Load_{p:04d}", "properties": {
            "description": f"Loads area {p} into dbo.{table}.", "activities": [
                {"name": "Copy load", "type": "Copy", "inputs": [ref(f"DS_Raw_{p:04d}")], "outputs": [ref(f"DS_{p:04d}")],
                 "typeProperties": {"source": {"type": "ParquetSource"}, "sink": {"type": "AzureSqlSink"}}},
                {"name": "Lookup watermark", "type": "Lookup", "dependsOn": [{"activity": "Copy load"}],
                 "typeProperties": {"dataset": ref(f"DS_{p:04d}"), "source": {"type": "AzureSqlSource"}}},
                {"name": "Wait", "type": "Wait", "dependsOn": [{"activity": "Lookup watermark"}],
                 "typeProperties": {"waitTimeInSeconds": 1}},
                {"name": "Set flag", "type": "SetVariable", "dependsOn": [{"activity": "Wait"}],
                 "typeProperties": {"variableName": "done", "value": "true"}},
                {"name": "Purge raw", "type": "Delete", "dependsOn": [{"activity": "Set flag"}],
                 "typeProperties": {"dataset": ref(f"DS_Raw_{p:04d}")}}]}})
    return folder


def main() -> int:
    scale = int(sys.argv[sys.argv.index("--scale") + 1]) if "--scale" in sys.argv else 1
    tmp = Path(tempfile.mkdtemp())
    try:
        app = create_app(Settings(local_data_dir=tmp / "data"), session_secret="s")
        client = TestClient(app, base_url="http://127.0.0.1:8765")
        headers = {"X-Bidoc-Session": "s", "X-Requested-With": "bidoc"}
        report = {"scale": scale, "documents": {}}

        def publish(label, engine, source, kind, key):
            t = time.monotonic()
            r = generate(GenerateRequest(engine=engine, source_path=str(source), source_kind=kind,
                                         output_dir=str(tmp / "out" / key), environment="Production"))
            assert r.status == "completed", r.errors
            gen = time.monotonic() - t
            data = Path(r.artifact_path).read_bytes()
            t = time.monotonic()
            res = client.post("/api/v1/imports", files={"file": ("d.html", data, "text/html")},
                              headers={**headers, "Idempotency-Key": key})
            assert res.status_code == 201, res.text
            report["documents"][label] = {"generate_seconds": round(gen, 2), "publish_seconds":
                                          round(time.monotonic() - t, 2), "artifact_bytes": len(data)}
            return res.json()

        tracemalloc.start()
        model = publish("power_bi_large", "power_bi", large_model(tmp / "pbi", scale), "bim", "pbi")
        adf = publish("adf_large", "adf", large_factory(tmp / "adf", scale), "adf_git", "adf")
        _, peak_publish = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()

        from bidoc_library.derived import Derived
        derived, store = Derived(app.state.store), app.state.store
        t = time.monotonic()                                  # a full rebuild from the catalogue, cold
        current, seq = derived._current(), store.read_sequence()
        derived._build_search(current, seq)
        derived._build_relationships(current, store.manual_active(), seq)
        rebuild = time.monotonic() - t
        _, peak_rebuild = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()

        rel = client.get(f"/api/v1/documents/{model['document_id']}/relationships").json()
        links = len(rel.get("incoming", [])) + len(rel.get("outgoing", []))
        index = client.get("/api/v1/search-index").json()
        index_bytes = len(json.dumps(index, separators=(",", ":")).encode())
        timings, mismatches = [], []
        for q in QUERIES:
            t = time.monotonic()
            local = search(index["documents"], q)
            timings.append((time.monotonic() - t) * 1000)
            server = client.get("/api/v1/search", params={"q": q, "limit": 500}).json()
            if server["total"] != len(local) or server["items"] != local[:500]:
                mismatches.append(q)
        _, peak_search = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        caps = client.get("/api/v1/capabilities").json()
        report.update({
            "rebuild_seconds": round(rebuild, 2), "relationship_links_on_model": links,
            "relationship_state": rel["generation"]["state"],
            "search_index_bytes": index_bytes, "search_mode": caps["search_mode"],
            "search_ms_per_query_max": round(max(timings), 1), "server_search_matches_browser": not mismatches,
            "peak_mib": {"publish": round(peak_publish / 2 ** 20), "rebuild": round(peak_rebuild / 2 ** 20),
                         "search": round(peak_search / 2 ** 20)}})
        print(json.dumps(report, indent=1))
        if "--json" in sys.argv:
            Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(report, indent=1))

        assert not mismatches, f"server search differs from browser search for {mismatches}"
        assert rel["generation"]["state"] == "ready" and links > 0, "the large documents should be linked"
        slow = [k for k, v in (("publish_seconds", max(d["publish_seconds"] for d in report["documents"].values())),
                               ("rebuild_seconds", rebuild), ("peak_mib", max(report["peak_mib"].values())),
                               ("search_ms", max(timings))) if v > BUDGET[k]]
        assert not slow, f"over budget: {slow}"
        _ = adf
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
