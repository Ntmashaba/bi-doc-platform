"""Build a realistic, explicitly synthetic Retail Sales sample for UI review.

Usage (from the repo root):
    python tests/samples/build_retail_sample.py OUTPUT_FOLDER
    python generate_docs.py --catalog OUTPUT_FOLDER

Produces "Retail Sales.html" (combined model + report, 3 pages incl. one hidden),
"Retail Sales.json" and "Finance Model.html" (model only), for screenshots and
before/after comparisons. Sources: SQL Server (FinanceDW), a SharePoint Excel
workbook and a UNC CSV. The line-chart binding Date[Calendar] is deliberately
malformed (a hierarchy name passed as a field): it reproduces the "one unresolved
binding turns every unused column into Review" defect described in
docs/ui-review-handover.md.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.renderer import build_payload, render_html  # noqa: E402
from pbidocgen.linker import link  # noqa: E402


def col(n, **k):
    return dict(name=n, **k)


def sql(tbl):
    return {"type": "m", "expression": (
        'let\n    Source = Sql.Database("finance-sql.corp.local", "FinanceDW"),\n'
        f'    T = Source{{[Schema="dbo",Item="{tbl}"]}}[Data],\n'
        '    Filtered = Table.SelectRows(T, each [IsDeleted] = false)\nin\n    Filtered')}


XL = {"type": "m", "expression": (
    'let\n    Source = Excel.Workbook(Web.Contents("https://contoso.sharepoint.com/sites/Finance/'
    'Shared Documents/Budget FY26.xlsx"), null, true),\n'
    '    Budget = Source{[Item="Budget",Kind="Table"]}[Data],\n'
    '    Typed = Table.TransformColumnTypes(Budget,{{"Amount", type number}})\nin\n    Typed')}
CSV = {"type": "m", "expression": (
    'let\n    Source = Csv.Document(File.Contents("\\\\fileserver\\exports\\targets.csv"),[Delimiter=","]),\n'
    '    Promoted = Table.PromoteHeaders(Source)\nin\n    Promoted')}

TABLES = [
    {"name": "Sales", "columns": [col(c) for c in ("OrderKey", "DateKey", "ProductKey", "CustomerKey", "StoreKey",
                                                   "Quantity", "NetAmount", "Cost", "DiscountAmount", "LegacyFlag",
                                                   "ETLBatchId")]
     + [col("Margin", type="calculated", expression="Sales[NetAmount]-Sales[Cost]")],
     "measures": [
         {"name": "Revenue", "expression": "SUM(Sales[NetAmount])", "formatString": "#,0",
          "description": "Net sales after discounts, excluding VAT."},
         # Deliberate duplicate of Revenue (formatting and a comment differ), unused.
         {"name": "Net Sales", "expression": "sum( Sales[NetAmount] )  // copied from Revenue"},
         {"name": "Total Cost", "displayFolder": "Profitability", "expression": "SUM(Sales[Cost])"},
         {"name": "Gross Margin", "displayFolder": "Profitability", "expression": "[Revenue]-[Total Cost]"},
         {"name": "Gross Margin %", "displayFolder": "Profitability", "expression": "DIVIDE([Gross Margin],[Revenue])", "formatString": "0.0%"},
         {"name": "Units", "expression": "SUM(Sales[Quantity])", "formatString": "#,0",
          "description": "Units sold."},
         {"name": "Revenue LY", "displayFolder": "Time intelligence", "expression": "CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date]))"},
         {"name": "Revenue YoY %", "displayFolder": "Time intelligence", "expression": "DIVIDE([Revenue]-[Revenue LY],[Revenue LY])"},
         {"name": "Revenue YTD", "displayFolder": "Time intelligence", "expression": "TOTALYTD([Revenue],'Date'[Date])"},
         {"name": "Avg Discount", "expression": "AVERAGE(Sales[DiscountAmount])"},
         {"name": "Orders", "expression": "DISTINCTCOUNT(Sales[OrderKey])"},
         {"name": "Avg Order Value", "expression": "DIVIDE([Revenue],[Orders])"}],
     "partitions": [{"name": "Sales", "mode": "import", "source": sql("FactSales")}]},
    {"name": "Date", "dataCategory": "Time",
     "columns": [col("Date"), col("DateKey"), col("Year"), col("Quarter"), col("Month"), col("MonthNo"),
                 col("MonthName", sortByColumn="MonthNo"), col("FiscalWeek")],
     "hierarchies": [{"name": "Calendar", "levels": [{"name": "Year", "column": "Year"},
                                                      {"name": "Quarter", "column": "Quarter"},
                                                      {"name": "Month", "column": "MonthName"}]}],
     "partitions": [{"name": "Date", "source": sql("DimDate")}]},
    {"name": "Product", "columns": [col(c) for c in ("ProductKey", "Product", "Category", "Subcategory", "Brand",
                                                     "Colour", "ListPrice", "SupplierCode")],
     "partitions": [{"name": "Product", "source": sql("DimProduct")}]},
    {"name": "Customer", "columns": [col(c) for c in ("CustomerKey", "Customer", "Segment", "Country", "City",
                                                      "Email", "Phone")],
     "partitions": [{"name": "Customer", "source": sql("DimCustomer")}]},
    {"name": "Store", "columns": [col(c) for c in ("StoreKey", "Store", "Region", "Manager", "ManagerEmail")],
     "partitions": [{"name": "Store", "source": sql("DimStore")}]},
    {"name": "Budget", "columns": [col("DateKey"), col("StoreKey"), col("Amount")],
     "measures": [{"name": "Budget", "expression": "SUM(Budget[Amount])"},
                  {"name": "Var to Budget", "expression": "[Revenue]-[Budget]"},
                  {"name": "Var to Budget %", "expression": "DIVIDE([Var to Budget],[Budget])"}],
     "partitions": [{"name": "Budget", "source": XL}]},
    {"name": "Targets", "columns": [col("Region"), col("Target")],
     "partitions": [{"name": "Targets", "source": CSV}]},
]
RELS = [("Sales", "DateKey", "Date", "DateKey"), ("Sales", "ProductKey", "Product", "ProductKey"),
        ("Sales", "CustomerKey", "Customer", "CustomerKey"), ("Sales", "StoreKey", "Store", "StoreKey"),
        ("Budget", "DateKey", "Date", "DateKey"), ("Budget", "StoreKey", "Store", "StoreKey")]
RAW = {"model": {"name": "Retail Sales", "culture": "en-ZA", "tables": TABLES,
                 "relationships": [{"name": f"r{i}", "fromTable": a, "fromColumn": b, "toTable": c, "toColumn": d}
                                   for i, (a, b, c, d) in enumerate(RELS)],
                 "roles": [{"name": "Regional Manager", "tablePermissions": [
                     {"name": "Store", "filterExpression": "[ManagerEmail] = USERPRINCIPALNAME()"}]}]}}


def F(t, f, k="column", **kw):
    return dict(table=t, field=f, kind=k, **kw)


def V(i, typ, title, fields, x, y, w, h):
    return {"id": i, "type": typ, "title": title, "fields": fields, "filters": [],
            "x": x, "y": y, "width": w, "height": h}


PAGES = [
    {"id": "ReportSection1", "name": "Executive Summary", "width": 1280, "height": 720,
     "filters": [F("Date", "Year")], "visuals": [
        V("k1", "card", "Revenue", [F("Sales", "Revenue", "measure")], 20, 20, 280, 120),
        V("k2", "card", "Gross Margin %", [F("Sales", "Gross Margin %", "measure")], 320, 20, 280, 120),
        V("k3", "card", "YoY", [F("Sales", "Revenue YoY %", "measure")], 620, 20, 280, 120),
        V("k4", "card", "Var to Budget", [F("Budget", "Var to Budget %", "measure")], 920, 20, 340, 120),
        V("c1", "lineChart", "Revenue vs LY", [F("Date", "Calendar", "hierarchyLevel", hierarchy="Calendar"),
                                               F("Date", "MonthName"), F("Sales", "Revenue", "measure"),
                                               F("Sales", "Revenue LY", "measure")], 20, 160, 760, 540),
        V("c2", "barChart", "Revenue by Region", [F("Store", "Region"), F("Sales", "Revenue", "measure"),
                                                  F("Budget", "Budget", "measure")], 800, 160, 460, 540)]},
    {"id": "ReportSection2", "name": "Product Performance", "width": 1280, "height": 720, "filters": [],
     "visuals": [
        V("s1", "slicer", None, [F("Product", "Category")], 20, 20, 300, 80),
        V("t1", "tableEx", "Products", [F("Product", "Product"), F("Product", "Brand"), F("Sales", "Units", "measure"),
                                        F("Sales", "Revenue", "measure"), F("Sales", "Gross Margin %", "measure"),
                                        F("Sales", "Avg Discount", "measure")], 20, 120, 820, 580),
        V("c3", "donutChart", "Mix by Subcategory", [F("Product", "Subcategory"), F("Sales", "Revenue", "measure")],
          860, 120, 400, 580)]},
    {"id": "ReportSection3", "name": "Customer Detail", "hidden": True, "width": 1280, "height": 720, "filters": [],
     "visuals": [V("t2", "tableEx", "Customers", [F("Customer", "Customer"), F("Customer", "Segment"),
                                                  F("Customer", "Country"), F("Sales", "Orders", "measure"),
                                                  F("Sales", "Avg Order Value", "measure")], 20, 20, 1240, 680)]},
]
REPORT = {"name": "Retail Sales", "pages": PAGES, "reportFilters": [],
          "bookmarks": [{"name": "Top brands", "fields": [F("Product", "Brand")]}], "manifest": [], "warnings": []}


def build_retail_model():
    """Parse the sample model exactly as the command line would."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "model.bim"
        p.write_text(json.dumps(RAW), encoding="utf-8")
        return parse_model(p)


def build_retail_payload(model=None):
    """The combined Retail Sales payload; also used by tests/test_retail_sample.py."""
    m = model or build_retail_model()
    return build_payload(m, REPORT, link(m, REPORT), "Retail Sales")


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    m = build_retail_model()
    payload = build_retail_payload(m)
    render_html(payload, out / "Retail Sales.html")
    (out / "Retail Sales.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    render_html(build_payload(m, None, None, "Finance Model"), out / "Finance Model.html")
    print("Wrote sample documentation to", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample-output")
