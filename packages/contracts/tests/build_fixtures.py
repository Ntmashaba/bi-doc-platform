"""Build the golden artifact fixtures in tests/fixtures/ deterministically.

    python tests/build_fixtures.py          # rewrite fixtures
    python tests/build_fixtures.py --check  # fail if fixtures on disk differ

Every valid fixture's hash is computed by embed_manifest(); none is hand-written.
Invalid fixtures are derived from valid bytes with one deliberate defect each.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bidoc_contracts import PLACEHOLDER, embed_manifest  # noqa: E402

OUT = Path(__file__).resolve().parent / "fixtures"
NULL_EP = dict.fromkeys(["system", "server", "instance", "port", "database", "schema", "object",
                         "storage_account", "container", "path", "url"])


def page(title, sections, newline="\n"):
    body = newline.join(f'<section id="{s["id"]}"><h2>{s["title"]}</h2><p>{s["text"]}</p></section>' for s in sections)
    html = (f'<!doctype html>{newline}<html lang="en">{newline}<head>{newline}<meta charset="utf-8">{newline}'
            f"<title>{title}</title>{newline}" + PLACEHOLDER.decode() + f"{newline}</head>{newline}<body>{newline}"
            f"{body}{newline}</body>{newline}</html>{newline}")
    return html.encode("utf-8")


def power_bi():
    ep = dict(NULL_EP, system="SQL Server", server="finance-sql.corp.local", port=1433,
              database="FinanceDW", schema="dbo", object="FactSales")
    sections = [
        {"id": "overview", "title": "Overview", "text": "Retail Sales semantic model and report."},
        {"id": "measure-revenue", "title": "Revenue", "text": "Revenue = SUM(Sales[NetAmount]). Net sales after discounts."},
        {"id": "source-factsales", "title": "Source: FinanceDW dbo.FactSales", "text": "SQL Server finance-sql.corp.local."},
    ]
    return {
        "schema_version": 1, "document_type": "power_bi",
        "classification": {"business_area": "Finance", "environment": "Production", "owner": "BI team"},
        "native_payload": {"schema_version": 2, "generator": "pbi-doc-gen 0.1.0", "data": {
            "title": "Retail Sales",
            "model": {"tables": [{"name": "Sales", "lineageTag": "1b2c3d4e-0000-4000-8000-000000000001",
                                  "measures": [{"name": "Revenue", "lineageTag": "1b2c3d4e-0000-4000-8000-000000000002"}]}]},
            "sourceObjects": [{"server": ep["server"], "database": ep["database"], "schema": "dbo", "object": "FactSales"}]}},
        "objects": [
            {"object_id": "pbi:table:1b2c3d4e-0000-4000-8000-000000000001", "kind": "table", "label": "Sales",
             "section_id": "overview", "parent_object_id": None, "bindings": [], "dynamic": False, "opaque": False,
             "coverage": "complete"},
            {"object_id": "pbi:measure:1b2c3d4e-0000-4000-8000-000000000002", "kind": "measure", "label": "Revenue",
             "section_id": "measure-revenue", "parent_object_id": "pbi:table:1b2c3d4e-0000-4000-8000-000000000001",
             "bindings": [], "dynamic": False, "opaque": False, "coverage": "complete"},
            {"object_id": "pbi:source:sqlserver:finance-sql.corp.local:1433:FinanceDW:dbo.FactSales", "kind": "source",
             "label": "FinanceDW dbo.FactSales", "section_id": "source-factsales", "parent_object_id": None,
             "bindings": [{"binding_id": "b1", "operation": "read", "endpoint": ep,
                           "normalized_endpoint": dict(ep, server="finance-sql.corp.local"),
                           "normalization_version": "endpoint-norm/1", "invocation_context": {"table": "Sales"},
                           "evidence_refs": ["/sourceObjects/0"], "resolution": "static", "coverage": "complete"}],
             "dynamic": False, "opaque": False, "coverage": "complete"},
        ],
        "document_id": "0f8b5c2e-6a1d-4c3e-9b7a-1d2e3f405061", "revision_id": "7a1c9e44-2b3d-4f5a-8c6d-0e1f2a3b4c5d",
        "title": "Retail Sales", "description": "Synthetic contract fixture.", "tags": ["finance", "sales"],
        "generated_at": "2026-09-28T12:00:00Z", "generator": {"name": "bi-doc-generator", "version": "0.1.0"},
        "source": {"kind": "pbip", "label": "Retail Sales.pbip"},
        "publication": {"asset_id": "3c2b1a09-8f7e-4d6c-a5b4-c3d2e1f00918", "environment_key": "production",
                        "scope_key": "full", "scope_descriptor": {"kind": "full_project"}, "snapshot_state": "complete"},
        "projection": {"policy_version": "shared-projection/1", "native_schema": "pbi-doc-gen/2", "profile": "shared",
                       "options": {"query_code": "withheld"},
                       "omissions": [{"path": "/model/tables/0/partitions/0/source/expression",
                                      "reason": "query_code_withheld", "effect": "M query text not published."}],
                       "coverage_warnings": []},
        "navigation": {"targets": [
            {"target_id": "pbi:measure:1b2c3d4e-0000-4000-8000-000000000002", "view_id": "pbi.measure",
             "args": {"table": "Sales", "measure": "Revenue"}},
            {"target_id": "overview", "view_id": "pbi.tab", "args": {"tab": "model"}}]},
        "identity_version": "pbi-identity/1", "content_sha256": "0" * 64, "sections": sections,
    }


def adf():
    ep = dict(NULL_EP, system="Azure Data Lake Storage Gen2", storage_account="contosolake", container="landing",
              path="sales/orders", url="https://contosolake.dfs.core.windows.net")
    sections = [{"id": "pipeline-pl-ingest-sales", "title": "PL_Ingest_Sales", "text": "Lands orders from SalesDW into the lake."},
                {"id": "activity-land-orders", "title": "Land orders", "text": "Copy activity."}]
    return {
        "schema_version": 1, "document_type": "adf",
        "classification": {"business_area": "Sales", "environment": "Production", "owner": ""},
        "native_payload": {"schema_version": 2, "generator": "adf-doc-gen 0.1.0",
                           "data": {"pipelines": [{"name": "PL_Ingest_Sales", "activities": [{"activity": "Land orders"}]}]}},
        "objects": [
            {"object_id": "adf:pipeline:PL_Ingest_Sales", "kind": "pipeline", "label": "PL_Ingest_Sales",
             "section_id": "pipeline-pl-ingest-sales", "parent_object_id": None, "bindings": [], "dynamic": False,
             "opaque": False, "coverage": "complete"},
            {"object_id": "adf:activity:PL_Ingest_Sales/Land orders", "kind": "activity", "label": "Land orders",
             "section_id": "activity-land-orders", "parent_object_id": "adf:pipeline:PL_Ingest_Sales",
             "bindings": [{"binding_id": "w1", "operation": "write", "endpoint": ep, "normalized_endpoint": ep,
                           "normalization_version": "endpoint-norm/1",
                           "invocation_context": {"pipeline": "PL_Ingest_Sales", "activity": "Land orders", "entity": "orders"},
                           "evidence_refs": ["/pipelines/0/activities/0"], "resolution": "static", "coverage": "complete"}],
             "dynamic": False, "opaque": False, "coverage": "complete"}],
        "document_id": "5d4c3b2a-1908-4f7e-8d6c-5b4a39281706", "revision_id": "a9b8c7d6-e5f4-4a3b-9c2d-1e0f9a8b7c6d",
        "title": "Contoso Sales ETL", "description": "", "tags": [], "generated_at": "2026-09-28T12:00:00.123Z",
        "generator": {"name": "bi-doc-generator", "version": "0.1.0"},
        "source": {"kind": "adf_git", "label": "contoso-sales-etl",
                   "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"},
        "publication": {"asset_id": "b1a2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", "environment_key": "production",
                        "scope_key": "factory", "scope_descriptor": {"kind": "factory"}, "snapshot_state": "complete"},
        "projection": {"policy_version": "shared-projection/1", "native_schema": "adf-doc-gen/2", "profile": "shared",
                       "options": {"query_code": "included"}, "omissions": [],
                       "coverage_warnings": ["1 activity has an unknown footprint."]},
        "navigation": {"targets": [{"target_id": "adf:activity:PL_Ingest_Sales/Land orders", "view_id": "adf.activity",
                                    "args": {"pipeline": "PL_Ingest_Sales", "activity": "Land orders"}}]},
        "identity_version": "adf-identity/1", "content_sha256": "0" * 64, "sections": sections,
    }


def build():
    files = {}
    pbi = power_bi()
    files["valid-power-bi.html"] = embed_manifest(page(pbi["title"], pbi["sections"]), pbi)
    a = adf()
    files["valid-adf-crlf.html"] = embed_manifest(page(a["title"], a["sections"], "\r\n"), a)

    tricky = power_bi()
    tricky["description"] = 'Contains </script><script id="pbidoc-manifest">alert(1)</script> as text.'
    tricky["sections"][0]["text"] = "Closing tag </SCRIPT > and <!-- comment --> inside section text."
    safe_sections = [dict(s, text="(escaped in manifest)") for s in tricky["sections"]]
    files["valid-escaped-script-text.html"] = embed_manifest(page(tricky["title"], safe_sections), tricky)

    bad = power_bi()
    bad["schema_version"] = 2
    files["invalid-unsupported-version.html"] = embed_manifest(page(bad["title"], bad["sections"]), bad)

    valid = files["valid-power-bi.html"]
    start = valid.index(b'<script type="application/json" id="pbidoc-manifest">')
    end = valid.index(b"</script>", start) + len(b"</script>")
    files["invalid-duplicate-manifest.html"] = valid[:end] + valid[start:end] + valid[end:]
    files["invalid-hash-mismatch.html"] = valid.replace(b"<p>Retail Sales semantic model", b"<p>Retail sales semantic model")

    missing = power_bi()
    shown = [s for s in missing["sections"] if s["id"] != "measure-revenue"]
    files["invalid-missing-anchor.html"] = embed_manifest(page(missing["title"], shown), missing)
    return files


def main():
    files = build()
    if "--check" in sys.argv:
        stale = [n for n, b in files.items() if not (OUT / n).is_file() or (OUT / n).read_bytes() != b]
        if stale:
            sys.exit(f"fixtures out of date: {stale}; run python tests/build_fixtures.py")
        return
    OUT.mkdir(exist_ok=True)
    for name, data in files.items():
        (OUT / name).write_bytes(data)
    print(json.dumps(sorted(files)))


if __name__ == "__main__":
    main()
