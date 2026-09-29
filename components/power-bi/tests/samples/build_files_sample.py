"""Build a synthetic "Field Services" sample with SharePoint and file sources.

Usage (from the repo root):
    python tests/samples/build_files_sample.py OUTPUT_FOLDER

Reproduces the M Power BI Desktop writes for SharePoint lists (SharePoint.Tables,
online and on-premises), SharePoint files (SharePoint.Files filtered to one file,
SharePoint.Contents navigation, Web.Contents to a document URL) and Windows files
(File.Contents on a local drive and a UNC share, Folder.Files with the generated
"Combine files" helper queries). All names are invented.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pbidocgen.linker import link  # noqa: E402
from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.renderer import build_payload, render_html  # noqa: E402

SITE = "https://contoso.sharepoint.com/sites/FieldOps"


def col(n, **k):
    return dict(name=n, **k)


def m(expression, mode="import"):
    return [{"name": "p", "mode": mode, "source": {"type": "m", "expression": expression}}]


# Queries Power BI adds for "Combine files" on a folder, plus a path parameter.
EXPRESSIONS = [
    {"name": "TimesheetFolder", "kind": "m",
     "expression": '"\\\\fs01.corp.example\\FieldOps\\Timesheets\\" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'},
    {"name": "Sample File", "kind": "m",
     "expression": 'let\n    Source = Folder.Files(TimesheetFolder),\n    Navigation1 = Source{0}[Content]\nin\n    Navigation1'},
    {"name": "Parameter1", "kind": "m",
     "expression": '#"Sample File" meta [IsParameterQuery=true, BinaryIdentifier=#"Sample File", Type="Binary", IsParameterQueryRequired=true]'},
    {"name": "Transform Sample File", "kind": "m",
     "expression": 'let\n    Source = Csv.Document(Parameter1,[Delimiter=",", Columns=5, Encoding=65001, QuoteStyle=QuoteStyle.None]),\n'
                   '    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true])\nin\n    #"Promoted Headers"'},
    {"name": "Transform File", "kind": "m",
     "expression": 'let\n    Source = (Parameter1 as binary) => let\n        Source = Csv.Document(Parameter1,[Delimiter=",", Columns=5, Encoding=65001, QuoteStyle=QuoteStyle.None]),\n'
                   '        #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true])\n    in\n        #"Promoted Headers"\nin\n    Source'},
]

TABLES = [
    # SharePoint Online list.
    {"name": "Work Orders", "columns": [col(c) for c in ("ID", "Title", "Status", "Technician", "SiteCode", "Opened",
                                                         "Closed")],
     "measures": [{"name": "Open Work Orders", "expression": "CALCULATE(COUNTROWS('Work Orders'), 'Work Orders'[Status] <> \"Closed\")"},
                  {"name": "Work Orders", "expression": "COUNTROWS('Work Orders')"}],
     "partitions": m(f'let\n    Source = SharePoint.Tables("{SITE}", [Implementation="2.0", ViewMode="All"]),\n'
                     '    #"Work Orders1" = Source{[Title="Work Orders"]}[Items],\n'
                     '    #"Removed Other Columns" = Table.SelectColumns(#"Work Orders1",{"ID", "Title", "Status", "Technician", '
                     '"SiteCode", "Opened", "Closed"})\nin\n    #"Removed Other Columns"')},
    # SharePoint list by GUID (the older 1.0 implementation).
    {"name": "Technician", "columns": [col(c) for c in ("Title", "Region", "Grade")],
     "partitions": m(f'let\n    Source = SharePoint.Tables("{SITE}", [ApiVersion = 15]),\n'
                     '    #"7c1b0c9e-2f0a-4c7b-9d1e-3a5b6c7d8e9f" = Source{[Id="7c1b0c9e-2f0a-4c7b-9d1e-3a5b6c7d8e9f"]}[Items]\n'
                     'in\n    #"7c1b0c9e-2f0a-4c7b-9d1e-3a5b6c7d8e9f"')},
    # On-premises SharePoint list.
    {"name": "Site", "columns": [col(c) for c in ("SiteCode", "SiteName", "Province")],
     "partitions": m('let\n    Source = SharePoint.Tables("https://sp2019.corp.example/sites/Assets", [ApiVersion = 15]),\n'
                     '    Sites = Source{[Title="Sites"]}[Items]\nin\n    Sites')},
    # SharePoint file: SharePoint.Files filtered to one workbook (what "SharePoint folder" writes).
    {"name": "Budget", "columns": [col(c) for c in ("SiteCode", "Month", "Budget")],
     "measures": [{"name": "Budget Amount", "expression": "SUM(Budget[Budget])"}],
     "partitions": m(f'let\n    Source = SharePoint.Files("{SITE}", [ApiVersion = 15]),\n'
                     f'    File = Source{{[Name="Budget FY26.xlsx",#"Folder Path"="{SITE}/Shared Documents/Finance/"]}}[Content],\n'
                     '    Workbook = Excel.Workbook(File, null, true),\n'
                     '    Budget_Sheet = Workbook{[Item="Budget",Kind="Sheet"]}[Data],\n'
                     '    #"Promoted Headers" = Table.PromoteHeaders(Budget_Sheet, [PromoteAllScalars=true])\nin\n    #"Promoted Headers"')},
    # SharePoint file via SharePoint.Contents navigation.
    {"name": "Price List", "columns": [col(c) for c in ("Part", "UnitPrice")],
     "partitions": m(f'let\n    Source = SharePoint.Contents("{SITE}", [ApiVersion = 15]),\n'
                     '    #"Shared Documents" = Source{[Name="Shared Documents"]}[Content],\n'
                     '    Procurement = #"Shared Documents"{[Name="Procurement"]}[Content],\n'
                     '    #"Price List csv" = Procurement{[Name="Price List.csv"]}[Content],\n'
                     '    #"Imported CSV" = Csv.Document(#"Price List csv",[Delimiter=",", Encoding=65001]),\n'
                     '    #"Promoted Headers" = Table.PromoteHeaders(#"Imported CSV")\nin\n    #"Promoted Headers"')},
    # SharePoint / OneDrive file opened by URL (what "Web" + a document link writes).
    {"name": "SLA Targets", "columns": [col(c) for c in ("Priority", "HoursToRespond")],
     "partitions": m('let\n    Source = Excel.Workbook(Web.Contents("https://contoso-my.sharepoint.com/personal/ops_lead_contoso_com/'
                     'Documents/SLA%20Targets.xlsx"), null, true),\n'
                     '    SLA = Source{[Item="SLA",Kind="Table"]}[Data]\nin\n    SLA')},
    # Windows: local drive.
    {"name": "Parts", "columns": [col(c) for c in ("Part", "Description", "Category")],
     "partitions": m('let\n    Source = Excel.Workbook(File.Contents("C:\\Data\\FieldOps\\Parts Catalogue.xlsx"), null, true),\n'
                     '    Parts_Table = Source{[Item="Parts",Kind="Table"]}[Data]\nin\n    Parts_Table')},
    # Windows: UNC share, CSV.
    {"name": "Vehicle", "columns": [col(c) for c in ("Registration", "Depot", "Type")],
     "partitions": m('let\n    Source = Csv.Document(File.Contents("\\\\fs01.corp.example\\FieldOps\\Fleet\\vehicles.csv"),'
                     '[Delimiter=",", Columns=3, Encoding=1252, QuoteStyle=QuoteStyle.None]),\n'
                     '    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true])\nin\n    #"Promoted Headers"')},
    # Windows: folder of CSVs combined with the generated helper queries.
    {"name": "Timesheet", "columns": [col(c) for c in ("Source.Name", "Technician", "Date", "Hours", "WorkOrder")],
     "measures": [{"name": "Hours Logged", "expression": "SUM(Timesheet[Hours])"}],
     "partitions": m('let\n    Source = Folder.Files(TimesheetFolder),\n'
                     '    #"Filtered Hidden Files1" = Table.SelectRows(Source, each [Attributes]?[Hidden]? <> true),\n'
                     '    #"Invoke Custom Function1" = Table.AddColumn(#"Filtered Hidden Files1", "Transform File", each #"Transform File"([Content])),\n'
                     '    #"Renamed Columns1" = Table.RenameColumns(#"Invoke Custom Function1", {"Name", "Source.Name"}),\n'
                     '    #"Removed Other Columns1" = Table.SelectColumns(#"Renamed Columns1", {"Source.Name", "Transform File"}),\n'
                     '    #"Expanded Table Column1" = Table.ExpandTableColumn(#"Removed Other Columns1", "Transform File", '
                     'Table.ColumnNames(#"Transform File"(#"Sample File")))\nin\n    #"Expanded Table Column1"')},
    # Windows: JSON file on a mapped drive.
    {"name": "Depot", "columns": [col(c) for c in ("Depot", "Manager")],
     "partitions": m('let\n    Source = Json.Document(File.Contents("H:\\Shared\\depots.json")),\n'
                     '    #"Converted to Table" = Table.FromList(Source, Splitter.SplitByNothing(), null, null, ExtraValues.Error),\n'
                     '    #"Expanded Column1" = Table.ExpandRecordColumn(#"Converted to Table", "Column1", {"Depot", "Manager"})\n'
                     'in\n    #"Expanded Column1"')},
]
RELS = [("Work Orders", "SiteCode", "Site", "SiteCode"), ("Work Orders", "Technician", "Technician", "Title"),
        ("Budget", "SiteCode", "Site", "SiteCode"), ("Timesheet", "Technician", "Technician", "Title"),
        ("Vehicle", "Depot", "Depot", "Depot")]
RAW = {"model": {"name": "Field Services", "culture": "en-ZA", "tables": TABLES, "expressions": EXPRESSIONS,
                 "relationships": [{"name": f"r{i}", "fromTable": a, "fromColumn": b, "toTable": c, "toColumn": d}
                                   for i, (a, b, c, d) in enumerate(RELS)]}}


def F(t, f, k="column"):
    return dict(table=t, field=f, kind=k)


def V(i, typ, title, fields, x, y, w, h):
    return {"id": i, "type": typ, "title": title, "fields": fields, "filters": [], "x": x, "y": y, "width": w, "height": h}


PAGES = [{"id": "ops", "name": "Operations", "width": 1280, "height": 720, "filters": [], "visuals": [
    V("o1", "card", "Open work orders", [F("Work Orders", "Open Work Orders", "measure")], 20, 20, 300, 120),
    V("o2", "card", "Hours logged", [F("Timesheet", "Hours Logged", "measure")], 340, 20, 300, 120),
    V("o3", "tableEx", "By site", [F("Site", "SiteName"), F("Work Orders", "Work Orders", "measure"),
                                   F("Budget", "Budget Amount", "measure")], 20, 160, 620, 540),
    V("o4", "tableEx", "Parts", [F("Parts", "Description"), F("Price List", "UnitPrice"), F("SLA Targets", "HoursToRespond"),
                                 F("Vehicle", "Registration"), F("Depot", "Manager")], 660, 20, 600, 680)]}]
REPORT = {"name": "Field Services", "pages": PAGES, "reportFilters": [], "bookmarks": [], "manifest": [], "warnings": []}


def build_model():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "model.bim"
        p.write_text(json.dumps(RAW), encoding="utf-8")
        return parse_model(p)


def build_files_payload(model=None):
    model = model or build_model()
    return build_payload(model, REPORT, link(model, REPORT), "Field Services")


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    render_html(build_files_payload(), out / "Field Services.html")
    print("Wrote", out / "Field Services.html")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample-output")
