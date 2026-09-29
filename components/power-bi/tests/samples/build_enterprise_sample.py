"""Build a synthetic "Network Operations" sample with enterprise sources.

Usage (from the repo root):
    python tests/samples/build_enterprise_sample.py OUTPUT_FOLDER

No public PBIX uses Teradata or Oracle, so this model reproduces the M patterns
Power BI Desktop writes for them (navigation, native SQL via [Query=...] and
Value.NativeQuery, parameters, ODBC DSNs), plus Databricks, a Power Platform
dataflow and DirectQuery/Dual storage. All names are invented.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pbidocgen.linker import link  # noqa: E402
from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.renderer import build_payload, render_html  # noqa: E402


def col(n, **k):
    return dict(name=n, **k)


def m(expression, mode="import"):
    return [{"name": "p", "mode": mode, "source": {"type": "m", "expression": expression}}]


PARAMETERS = [
    {"name": "TeradataServer", "kind": "m",
     "expression": '"tdprod.corp.example" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'},
    {"name": "OracleService", "kind": "m",
     "expression": '"ora-bill.corp.example:1521/BILLPRD" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'},
    # A shared staging query other tables build on.
    {"name": "TD EDW", "kind": "m",
     "expression": 'let\n    Source = Teradata.Database(TeradataServer, [HierarchicalNavigation=true]),\n'
                   '    EDW = Source{[Schema="EDW_PROD"]}[Data]\nin\n    EDW'},
]

TABLES = [
    # Teradata: navigation through a shared query, DirectQuery.
    {"name": "Network Usage", "columns": [col(c) for c in ("CELL_ID", "USAGE_DT", "SUBSCRIBER_ID", "DATA_MB",
                                                           "VOICE_MIN", "DROPPED_CALLS")],
     "measures": [{"name": "Data (TB)", "expression": "DIVIDE(SUM('Network Usage'[DATA_MB]), 1048576)"},
                  {"name": "Voice Minutes", "expression": "SUM('Network Usage'[VOICE_MIN])"},
                  {"name": "Drop Rate", "expression": "DIVIDE(SUM('Network Usage'[DROPPED_CALLS]), [Voice Minutes])",
                   "formatString": "0.00%"}],
     "partitions": m('let\n    Usage = #"TD EDW"{[Name="USAGE_DAILY"]}[Data],\n'
                     '    Recent = Table.SelectRows(Usage, each [USAGE_DT] >= #date(2025, 1, 1))\nin\n    Recent',
                     "directQuery")},
    # Teradata: native SQL in the connector options.
    {"name": "Subscriber", "columns": [col(c) for c in ("SUBSCRIBER_ID", "PLAN_CODE", "REGION", "ACTIVATION_DT",
                                                        "CHURN_FLAG")],
     "partitions": m('let\n    Source = Teradata.Database("tdprod.corp.example", [Query="SELECT s.SUBSCRIBER_ID, s.PLAN_CODE, '
                     'r.REGION, s.ACTIVATION_DT, s.CHURN_FLAG#(lf)FROM EDW_PROD.SUBSCRIBER s#(lf)JOIN EDW_PROD.REGION_LKP r '
                     'ON r.REGION_CD = s.REGION_CD#(lf)WHERE s.STATUS <> \'X\'"])\nin\n    Source', "dual")},
    # Teradata over ODBC (DSN).
    {"name": "Cell Site", "columns": [col(c) for c in ("CELL_ID", "SITE_NAME", "TECHNOLOGY", "LAT", "LON")],
     "partitions": m('let\n    Source = Odbc.Query("dsn=TD_PROD", "SELECT CELL_ID, SITE_NAME, TECHNOLOGY, LAT, LON '
                     'FROM EDW_PROD.CELL_SITE")\nin\n    Source')},
    # Oracle: navigation with a parameterised service.
    {"name": "Invoice", "columns": [col(c) for c in ("INVOICE_ID", "SUBSCRIBER_ID", "INVOICE_DT", "AMOUNT",
                                                     "CURRENCY")],
     "measures": [{"name": "Billed", "expression": "SUM(Invoice[AMOUNT])", "formatString": "#,0"}],
     "partitions": m('let\n    Source = Oracle.Database(OracleService, [HierarchicalNavigation=true]),\n'
                     '    BILLING = Source{[Schema="BILLING"]}[Data],\n'
                     '    INVOICE = BILLING{[Name="INVOICE"]}[Data],\n'
                     '    Kept = Table.SelectColumns(INVOICE, {"INVOICE_ID", "SUBSCRIBER_ID", "INVOICE_DT", "AMOUNT", "CURRENCY"})\n'
                     'in\n    Kept')},
    # Oracle: Value.NativeQuery with folding.
    {"name": "Payment", "columns": [col(c) for c in ("PAYMENT_ID", "INVOICE_ID", "PAID_DT", "PAID_AMOUNT")],
     "measures": [{"name": "Collected", "expression": "SUM(Payment[PAID_AMOUNT])", "formatString": "#,0"},
                  {"name": "Collection %", "expression": "DIVIDE([Collected], [Billed])", "formatString": "0.0%"}],
     "partitions": m('let\n    Source = Oracle.Database("ora-bill.corp.example:1521/BILLPRD"),\n'
                     '    Paid = Value.NativeQuery(Source, "SELECT PAYMENT_ID, INVOICE_ID, PAID_DT, PAID_AMOUNT '
                     'FROM BILLING.PAYMENT WHERE PAID_DT >= DATE \'2025-01-01\'", null, [EnableFolding=true])\n'
                     'in\n    Paid')},
    # Oracle via a TNS alias, older connector form.
    {"name": "Tariff", "columns": [col(c) for c in ("PLAN_CODE", "PLAN_NAME", "MONTHLY_FEE")],
     "partitions": m('let\n    Source = Oracle.Database("BILLPRD", [Query="select plan_code, plan_name, monthly_fee from '
                     'billing.tariff_plan"])\nin\n    Source')},
    # Databricks Unity Catalog.
    {"name": "Cell KPI", "columns": [col(c) for c in ("CELL_ID", "KPI_DATE", "AVAILABILITY", "THROUGHPUT")],
     "partitions": m('let\n    Source = Databricks.Catalogs("adb-1234567890.12.azuredatabricks.net", '
                     '"/sql/1.0/warehouses/abc123", [Catalog=null, Database=null]),\n'
                     '    main = Source{[Name="main",Kind="Database"]}[Data],\n'
                     '    network = main{[Name="network",Kind="Schema"]}[Data],\n'
                     '    kpi = network{[Name="cell_kpi_daily",Kind="Table"]}[Data]\nin\n    kpi')},
    # Power Platform dataflow.
    {"name": "Region Target", "columns": [col(c) for c in ("REGION", "TARGET_TB")],
     "partitions": m('let\n    Source = PowerPlatform.Dataflows(null),\n'
                     '    Workspaces = Source{[Id="Workspaces"]}[Data],\n'
                     '    WS = Workspaces{[workspaceId="11111111-2222-3333-4444-555555555555"]}[Data],\n'
                     '    DF = WS{[dataflowId="66666666-7777-8888-9999-000000000000"]}[Data],\n'
                     '    Targets = DF{[entity="RegionTargets",version=""]}[Data]\nin\n    Targets')},
    {"name": "Date", "dataCategory": "Time", "columns": [col("Date"), col("Year"), col("Month")],
     "partitions": [{"name": "d", "source": {"type": "calculated",
                                             "expression": "CALENDAR(DATE(2025,1,1), DATE(2026,12,31))"}}]},
]
RELS = [("Network Usage", "CELL_ID", "Cell Site", "CELL_ID"), ("Network Usage", "SUBSCRIBER_ID", "Subscriber", "SUBSCRIBER_ID"),
        ("Network Usage", "USAGE_DT", "Date", "Date"), ("Invoice", "SUBSCRIBER_ID", "Subscriber", "SUBSCRIBER_ID"),
        ("Payment", "INVOICE_ID", "Invoice", "INVOICE_ID"), ("Subscriber", "PLAN_CODE", "Tariff", "PLAN_CODE"),
        ("Cell KPI", "CELL_ID", "Cell Site", "CELL_ID")]
RAW = {"model": {"name": "Network Operations", "culture": "en-ZA", "tables": TABLES, "expressions": PARAMETERS,
                 "relationships": [{"name": f"r{i}", "fromTable": a, "fromColumn": b, "toTable": c, "toColumn": d}
                                   for i, (a, b, c, d) in enumerate(RELS)]}}


def F(t, f, k="column"):
    return dict(table=t, field=f, kind=k)


def V(i, typ, title, fields, x, y, w, h):
    return {"id": i, "type": typ, "title": title, "fields": fields, "filters": [], "x": x, "y": y, "width": w, "height": h}


PAGES = [
    {"id": "network", "name": "Network", "width": 1280, "height": 720, "filters": [], "visuals": [
        V("n1", "card", "Data (TB)", [F("Network Usage", "Data (TB)", "measure")], 20, 20, 300, 120),
        V("n2", "card", "Drop rate", [F("Network Usage", "Drop Rate", "measure")], 340, 20, 300, 120),
        V("n3", "lineChart", "Data by month", [F("Date", "Month"), F("Network Usage", "Data (TB)", "measure")], 20, 160, 620, 540),
        V("n4", "tableEx", "Cells", [F("Cell Site", "SITE_NAME"), F("Cell Site", "TECHNOLOGY"),
                                     F("Cell KPI", "AVAILABILITY"), F("Network Usage", "Drop Rate", "measure")], 660, 20, 600, 680)]},
    {"id": "billing", "name": "Billing", "width": 1280, "height": 720, "filters": [F("Subscriber", "REGION")], "visuals": [
        V("b1", "card", "Billed", [F("Invoice", "Billed", "measure")], 20, 20, 300, 120),
        V("b2", "card", "Collection %", [F("Payment", "Collection %", "measure")], 340, 20, 300, 120),
        V("b3", "barChart", "Billed by plan", [F("Tariff", "PLAN_NAME"), F("Invoice", "Billed", "measure")], 20, 160, 620, 540),
        V("b4", "tableEx", "Targets", [F("Region Target", "REGION"), F("Region Target", "TARGET_TB")], 660, 160, 600, 540)]},
]
REPORT = {"name": "Network Operations", "pages": PAGES, "reportFilters": [], "bookmarks": [], "manifest": [], "warnings": []}


def build_model():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "model.bim"
        p.write_text(json.dumps(RAW), encoding="utf-8")
        return parse_model(p)


def build_enterprise_payload(model=None):
    model = model or build_model()
    return build_payload(model, REPORT, link(model, REPORT), "Network Operations")


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    render_html(build_enterprise_payload(), out / "Network Operations.html")
    print("Wrote", out / "Network Operations.html")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample-output")
