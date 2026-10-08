"""The Power Query view over every kind of input the generator reads.

One test per input kind: modern and legacy PBIX, PBIP/TMDL, model.bim, ABF, pbi-tools extract folders and thin
reports; then the shapes of M that vary inside them (shared queries, several partitions, a query read by several
tables, nested M, expressions without let). Real files are used where the repository carries one; the rest are
built here in the layout the real format uses.
"""
import base64
import io
import json
import shutil
import sqlite3
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(ROOT))

from pbidocgen import input_limits, legacy_mashup, portable  # noqa: E402
from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.pbix_batch import load_extracted  # noqa: E402
from pbidocgen.power_query import power_query_view  # noqa: E402
from pbidocgen.project import discover  # noqa: E402
from pbidocgen.renderer import build_payload, render_html  # noqa: E402
from pbidocgen.report_parser import parse_report  # noqa: E402
from pbidocgen.linker import link  # noqa: E402

REAL_PBIX = REPO / "apps" / "generator" / "tests" / "fixtures" / "dp500-08-composite.pbix"
SAMPLES = ROOT / "pbip-samples"

SECTION = '''section Section1;

shared Age = let
    Source = Csv.Document(File.Contents(Folder & "\\age.csv"), [Delimiter = ","]),
    #"Promoted Headers" = Table.PromoteHeaders(Source),
    #"Changed Type" = Table.TransformColumnTypes(#"Promoted Headers", {{"Age", Int64.Type}})
in
    #"Changed Type";

[ Description = "Where the files are" ]
shared Folder = "\\\\share\\data" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true];

shared #"Not loaded" = let
    Source = OData.Feed("https://services.odata.org/V4/Northwind/"),
    Orders = Source{[Name="Orders",Signature="table"]}[Data]
in
    Orders;

shared Übersicht = let Source = Age in Source;
'''


def package_zip(section):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Config/Package.xml", "<Package/>")
        archive.writestr("Formulas/Section1.m", section)
    return buffer.getvalue()


def data_mashup(section, entries=None, groups=None):
    """A DataMashup stream in the layout of [MS-QDEFF] 2.2, the one a pre-2019 PBIX carries: version, then
    package, permissions, metadata and permission bindings, each after its length. The layout was checked by
    hand against the DataMashup of two real PBIX files before this builder was written."""
    package = package_zip(section)
    permissions = b'<?xml version="1.0" encoding="utf-8"?><PermissionList/>'
    items = ['<Item><ItemLocation><ItemType>AllFormulas</ItemType><ItemPath /></ItemLocation><StableEntries>'
             + (f'<Entry Type="QueryGroups" Value="s{json.dumps(groups).replace(chr(34), "&quot;")}" />' if groups else "")
             + '</StableEntries></Item>']
    for name, facts in (entries or {}).items():
        items.append(f'<Item><ItemLocation><ItemType>Formula</ItemType><ItemPath>Section1/{name}</ItemPath></ItemLocation><StableEntries>'
                     + "".join(f'<Entry Type="{k}" Value="{v}" />' for k, v in facts.items()) + '</StableEntries></Item>')
    xml = ('﻿<?xml version="1.0" encoding="utf-8"?><LocalPackageMetadataFile><Items>' + "".join(items)
           + "</Items></LocalPackageMetadataFile>").encode("utf-8")
    metadata = struct.pack("<II", 0, len(xml)) + xml + b"\x00" * 4
    return (struct.pack("<I", 0) + struct.pack("<I", len(package)) + package + struct.pack("<I", len(permissions)) + permissions
            + struct.pack("<I", len(metadata)) + metadata + struct.pack("<I", 0))


def metadata_db(statements, schema=None):
    db = sqlite3.connect(":memory:")
    db.executescript(schema or '''
CREATE TABLE Model (ID, Name, DefaultMode, Culture);
CREATE TABLE "Table" (ID, Name, Description, IsHidden, DataCategory, LineageTag, SystemFlags, CalculationGroupID);
CREATE TABLE Column (ID, TableID, Type, SortByColumnID, ExplicitName, InferredName, ExplicitDataType, InferredDataType);
CREATE TABLE Partition (ID, TableID, Name, Type, Mode, QueryDefinition, DataSourceID, QueryGroupID);
CREATE TABLE Measure (ID, TableID, Name, Expression, FormatString, IsHidden);
CREATE TABLE Annotation (ObjectID, Name, Value);
CREATE TABLE Expression (ID, ModelID, Name, Description, Kind, Expression, QueryGroupID, LineageTag);
CREATE TABLE QueryGroup (ID, ModelID, Folder, Description);
CREATE TABLE DataSource (ID, ModelID, Name, Type, ConnectionString, ConnectionDetails);
''')
    for statement, args in statements:
        db.execute(statement, args)
    db.commit()
    raw = db.serialize()
    db.close()
    return raw


class Corpus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def view(self, model, report=None):
        payload = build_payload(model, report, link(model, report) if model and report else None, "Corpus")
        view = power_query_view(payload)
        names = [q["name"] for q in view["queries"]]
        self.assertEqual(len(names), len(set(zip(names, (q["objectId"] for q in view["queries"])))), "one entry per query")
        self.assertEqual(len({q["objectId"] for q in view["queries"]}), len(names), "ids are unique")
        render_html(payload, self.tmp / "out.html")                    # the page renders for this input
        return {q["name"]: q for q in view["queries"]}, view, payload

    def bim(self, doc):
        path = self.tmp / "model.bim"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return parse_model(path)

    def portable_model(self, raw, path):
        with mock.patch.object(portable, "_metadata", side_effect=lambda *a, **k: input_limits.open_metadata(raw)):
            document = portable.model_document(path)
        target = self.tmp / "portable.bim"
        target.write_text(json.dumps(document), encoding="utf-8")
        return parse_model(target)

    # ---------------------------------------------------------------- PBIX
    @unittest.skipUnless(REAL_PBIX.is_file() and portable.available(), "needs the committed PBIX and pbixray")
    def test_modern_pbix_real_file(self):
        """DP500 08 Composite model.pbix, read directly: three parameters and one query per table."""
        queries, view, _ = self.view(portable.load_model(REAL_PBIX))
        self.assertEqual(sorted(n for n, q in queries.items() if q.get("kind") == "parameter"),
                         ["Culture", "SqlServerDatabase", "SqlServerInstance"])
        self.assertTrue(all(q["extraction"]["status"] == "complete" for q in queries.values()))
        self.assertEqual({q["steps"]["status"] for q in queries.values()}, {"parsed", "none"})
        self.assertEqual({q["publication"] for q in queries.values()}, {"included"})
        product = queries["Product"]
        self.assertEqual((product["origin"], product["load"], product["table"]), ("table", "loaded", "Product"))
        first = product["steps"]["items"][0]
        self.assertEqual((first["name"], first["says"]),
                         ("Source", "Connects to SQL Server: server SqlServerInstance, database SqlServerDatabase"))
        self.assertIn("Product", queries["SqlServerInstance"]["usedBy"])
        self.assertEqual(view["groups"], [])                                   # this file records no query folders
        code = next(r["mCode"] for r in self.view(portable.load_model(REAL_PBIX))[2]["sourceQueries"] if r["queryName"] == "Product")
        for step in product["steps"]["items"]:
            self.assertTrue(code[step["s"]:step["e"]].strip())

    def test_modern_pbix_metadata(self):
        raw = metadata_db([
            ("INSERT INTO Model VALUES (1, 'Shop', 0, 'en-US')", ()),
            ('INSERT INTO "Table" VALUES (10, \'Sales\', NULL, 0, NULL, \'t-1\', 0, NULL)', ()),
            ("INSERT INTO Column VALUES (11, 10, 1, NULL, 'Amount', NULL, 8, 8)", ()),
            ("INSERT INTO QueryGroup VALUES (20, 1, 'Staging', 'Read first')", ()),
            ("INSERT INTO Partition VALUES (30, 10, 'Sales-9c', 4, 0, ?, NULL, NULL)",
             ('let\n    Source = Stage,\n    #"Kept" = Table.SelectRows(Source, each [Year] = Year)\nin\n    #"Kept"',)),
            ("INSERT INTO Expression VALUES (40, 1, 'Stage', NULL, 0, ?, 20, 'e-1')",
             ('let Source = Sql.Database("srv", "dw"), T = Source{[Schema="dbo",Item="Sales"]}[Data] in T',)),
            ("INSERT INTO Expression VALUES (41, 1, 'Year', NULL, 0, ?, NULL, NULL)", ('2024 meta [IsParameterQuery=true, Type="Number"]',)),
            ("INSERT INTO Annotation VALUES (1, 'PBI_QueryOrder', ?)", ('["Year","Stage","Sales"]',)),
            ("INSERT INTO Annotation VALUES (20, 'PBI_QueryGroupOrder', '0')", ())])
        with zipfile.ZipFile(self.tmp / "modern.pbix", "w") as archive:        # a current PBIX has no DataMashup part
            archive.writestr("Version", "1.28")
        queries, view, _ = self.view(self.portable_model(raw, self.tmp / "modern.pbix"))
        self.assertEqual([q["name"] for q in sorted(queries.values(), key=lambda q: q["order"])], ["Year", "Stage", "Sales"])
        self.assertEqual((queries["Stage"]["group"], queries["Stage"]["objectId"], queries["Stage"]["usedBy"]),
                         ("Staging", "pbi:query:e-1", ["Sales"]))
        self.assertEqual([(g["folder"], g["description"]) for g in view["groups"]], [("Staging", "Read first")])
        self.assertEqual([s.get("says") for s in queries["Sales"]["steps"]["items"]],
                         ["Starts from the query Stage", "Keeps rows where [Year] = Year"])
        self.assertEqual(queries["Year"]["kind"], "parameter")

    def legacy_statements(self):
        age_only = "section Section1;\n\n" + SECTION.split("\n\n")[1] + "\n"
        connection = ("Provider=Microsoft.PowerBI.OleDb;Global Pipe=7a;Mashup=\""
                      + base64.b64encode(struct.pack("<II", 0, len(package_zip(age_only))) + package_zip(age_only)).decode() + "\";Location=Age")
        return [
            ("INSERT INTO Model VALUES (1, 'Old', 0, 'en-US')", ()),
            ('INSERT INTO "Table" VALUES (10, \'Age\', NULL, 0, NULL, NULL, 0, NULL)', ()),
            ("INSERT INTO Column VALUES (11, 10, 1, NULL, 'Age', NULL, 6, 6)", ()),
            ("INSERT INTO DataSource VALUES (50, 1, 'ds-age', 1, ?, NULL)", (connection,)),
            ("INSERT INTO Partition VALUES (30, 10, 'Age-1', 1, 0, 'SELECT * FROM [Age]', 50, NULL)", ())]

    def test_legacy_pbix(self):
        """A pre-2019 PBIX: the model holds a placeholder per table; the queries are in the DataMashup part."""
        pbix = self.tmp / "legacy.pbix"
        groups = [{"Id": "g-1", "Name": "Inputs", "Description": "Typed in", "ParentId": None, "Order": 0},
                  {"Id": "g-2", "Name": "Files", "Description": None, "ParentId": "g-1", "Order": 1}]
        with zipfile.ZipFile(pbix, "w") as archive:
            archive.writestr("Version", "1.0")
            archive.writestr("DataMashup", data_mashup(SECTION, {
                "Age": {"IsPrivate": "l0", "ResultType": "sTable", "FillEnabled": "l1"},
                "Folder": {"IsPrivate": "l0", "ResultType": "sText", "QueryGroupID": "sg-2"},
                "Not loaded": {"IsPrivate": "l0", "ResultType": "sTable", "FillEnabled": "l0", "QueryGroupID": "sg-1"},
                "Age/Changed Type": {"IsPrivate": "l0"}}, groups))
        queries, view, payload = self.view(self.portable_model(metadata_db(self.legacy_statements()), pbix))
        self.assertEqual(sorted(queries), ["Age", "Folder", "Not loaded", "Übersicht"])
        age = queries["Age"]
        self.assertEqual((age["origin"], age["load"], age["table"], age["extraction"]["status"]), ("table", "loaded", "Age", "complete"))
        self.assertEqual([s["name"] for s in age["steps"]["items"]], ["Source", "Promoted Headers", "Changed Type"])
        self.assertEqual(age["steps"]["items"][2]["says"], "Sets the data type of 1 column: Age (whole number)")
        # Queries no table reads exist only in the package, and are listed from it.
        self.assertEqual((queries["Not loaded"]["load"], queries["Not loaded"]["origin"]), ("not loaded", "shared"))
        self.assertEqual((queries["Folder"]["kind"], queries["Folder"]["description"]), ("parameter", "Where the files are"))
        self.assertEqual(queries["Folder"]["usedBy"], ["Age"])
        self.assertEqual(queries["Übersicht"]["upstream"], [age["objectId"]])
        # Folders recorded by the package are recreated, nested as recorded.
        self.assertEqual([g["folder"] for g in view["groups"]], ["Inputs", "Inputs\\Files"])
        self.assertEqual((queries["Folder"]["group"], queries["Not loaded"]["group"]), ("Inputs\\Files", "Inputs"))
        self.assertNotIn("SELECT * FROM [Age]", json.dumps(payload["sourceQueries"]))
        self.assertNotIn("mashupPackage", json.dumps(payload))                  # the package is read, never copied out

    def test_legacy_pbix_without_a_readable_package_still_lists_what_the_tables_hold(self):
        pbix = self.tmp / "legacy.pbix"
        for part in (None, b"not a package", struct.pack("<II", 0, 10 ** 9), struct.pack("<II", 7, 4) + b"PK\x03\x04"):
            with zipfile.ZipFile(pbix, "w") as archive:
                archive.writestr("Version", "1.0")
                if part is not None:
                    archive.writestr("DataMashup", part)
            self.assertIsNone(legacy_mashup.pbix_package(pbix))
            queries, _, _ = self.view(self.portable_model(metadata_db(self.legacy_statements()), pbix))
            self.assertEqual(list(queries), ["Age"])                           # from the table's own data source
            self.assertEqual(queries["Age"]["extraction"]["status"], "complete")

    def test_a_data_mashup_in_an_unexpected_form_is_ignored_not_trusted(self):
        good = data_mashup(SECTION)
        self.assertEqual(sorted(legacy_mashup.members(legacy_mashup.read_data_mashup(good)["section"])),
                         ["Age", "Folder", "Not loaded", "Übersicht"])
        package = package_zip(SECTION)
        bomb = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><LocalPackageMetadataFile><Items/></LocalPackageMetadataFile>').encode()
        metadata = struct.pack("<II", 0, len(bomb)) + bomb
        hostile = struct.pack("<II", 0, len(package)) + package + struct.pack("<I", 0) + struct.pack("<I", len(metadata)) + metadata
        read = legacy_mashup.read_data_mashup(hostile)
        self.assertEqual((read["queries"], read["groups"]), ({}, []))           # the section is kept; the metadata is not parsed
        truncated = legacy_mashup.read_data_mashup(good[:len(good) // 2])
        self.assertIsNone(truncated)
        odd_groups = data_mashup(SECTION, {"Age": {"QueryGroupID": "sg-1"}}, groups={"not": "a list"})
        self.assertEqual(legacy_mashup.read_data_mashup(odd_groups)["groups"], [])

    # ---------------------------------------------------------------- ABF
    def test_abf(self):
        """A Tabular backup: Power Query partitions are queries; partitions that are SQL against a provider are not."""
        raw = metadata_db([
            ("INSERT INTO Model VALUES (1, 'Tabular', 0, 'en-US')", ()),
            ('INSERT INTO "Table" VALUES (10, \'Sales\', NULL, 0, NULL, NULL, 0, NULL)', ()),
            ('INSERT INTO "Table" VALUES (12, \'Budget\', NULL, 0, NULL, NULL, 0, NULL)', ()),
            ("INSERT INTO Column VALUES (11, 10, 1, NULL, 'Amount', NULL, 8, 8)", ()),
            ("INSERT INTO Column VALUES (13, 12, 1, NULL, 'Amount', NULL, 8, 8)", ()),
            ("INSERT INTO DataSource VALUES (50, 1, 'dw', 1, 'Provider=SQLNCLI11;Data Source=srv;Initial Catalog=dw', NULL)", ()),
            ("INSERT INTO Partition VALUES (30, 10, 'Sales 2023', 4, 0, ?, NULL, NULL)", ('let S = #"SQL/srv;dw", T = S{[Schema="dbo",Item="Sales2023"]}[Data] in T',)),
            ("INSERT INTO Partition VALUES (31, 10, 'Sales 2024', 4, 0, ?, NULL, NULL)", ('let S = #"SQL/srv;dw", T = S{[Schema="dbo",Item="Sales2024"]}[Data] in T',)),
            ("INSERT INTO Partition VALUES (32, 12, 'Budget', 1, 0, 'SELECT * FROM dbo.Budget', 50, NULL)", ())])
        queries, _, _ = self.view(self.portable_model(raw, "model.abf"))
        self.assertEqual(sorted(queries), ["Sales / Sales 2023", "Sales / Sales 2024"])
        self.assertEqual(queries["Sales / Sales 2023"]["partitions"], ["Sales 2023"])
        self.assertEqual(queries["Sales / Sales 2024"]["steps"]["items"][1]["says"], "Navigates to dbo.Sales2024")

    # ---------------------------------------------------------------- PBIP / TMDL
    def test_pbip_tmdl_real_project(self):
        """The committed DirectQuery-to-Analysis-Services project: one shared expression the entity tables read through."""
        found = discover(SAMPLES / "directquery-to-analysis-services")
        model = parse_model(found["model"])
        report = parse_report(found["report"]) if found["report"] else None
        queries, _, _ = self.view(model, report)
        self.assertEqual(sorted(queries), ["DirectQuery to AS - 16-Starter-Sales Analysis", "US Population"])
        local = queries["US Population"]                           # the one table this composite model imports itself
        self.assertEqual((local["origin"], local["load"], local["steps"]["status"]), ("table", "loaded", "parsed"))
        query = queries["DirectQuery to AS - 16-Starter-Sales Analysis"]
        self.assertEqual((query["origin"], query["load"], query["objectId"]),
                         ("shared", "not loaded", "pbi:query:fc4c2148-0507-40ae-b970-4d06348fa82d"))
        self.assertEqual(sorted(query["usedBy"]), sorted(t["name"] for t in model["tables"] if any(
            (p.get("source") or {}).get("expressionSource") for p in t["partitions"])))
        self.assertTrue(query["usedBy"])
        self.assertEqual([(s["name"], s.get("says", "")) for s in query["steps"]["items"]], [
            ("Source", "Connects to Analysis Services: server powerbi://api.powerbi.com/v1.0/myorg/YourName, database 16-Starter-Sales Analysis"),
            ("Cubes", ""), ("Cube", "Navigates to the cube Model")])

    def test_tmdl_expression_blocks_keep_their_lines_and_indentation(self):
        definition = self.tmp / "T.SemanticModel" / "definition"
        (definition / "tables").mkdir(parents=True)
        (definition / "model.tmdl").write_text("model Model\n", encoding="utf-8")
        script = ('let\n    Source = Sql.Database("srv", "dw"),\n    // keep 2024\n    Kept = Table.SelectRows(Source, each\n'
                  '        [Year] = 2024\n            and [Region] <> "let"),\n    Result = Kept\nin\n    Result')
        block = "\n".join("\t\t\t\t" + line for line in script.split("\n"))
        (definition / "tables" / "Sales.tmdl").write_text(
            f"table Sales\n\n\tcolumn Amount\n\t\tdataType: double\n\n\tpartition Sales = m\n\t\tmode: import\n\t\tsource =\n{block}\n",
            encoding="utf-8")
        queries, _, payload = self.view(parse_model(definition.parent))
        self.assertEqual(payload["sourceQueries"][0]["mCode"], script)
        steps = queries["Sales"]["steps"]["items"]
        self.assertEqual([s["name"] for s in steps], ["Source", "Kept", "Result"])
        self.assertEqual((steps[1]["note"], steps[2]["says"]), ("keep 2024", "Same as the step Kept"))
        self.assertEqual(script[steps[1]["s"]:steps[1]["e"]], 'Table.SelectRows(Source, each\n        [Year] = 2024\n            and [Region] <> "let")')

    # ---------------------------------------------------------------- model.bim
    def test_bim_expressions_written_as_lists_of_lines(self):
        model = self.bim({"model": {"name": "B", "tables": [{"name": "T", "columns": [{"name": "A"}], "partitions": [{
            "name": "T", "source": {"type": "m", "expression": ["let", "    Source = Stage,", "    #\"Removed\" = Table.RemoveColumns(Source,{\"x\"})",
                                                             "in", "    #\"Removed\""]}}]}],
            "expressions": [{"name": "Stage", "kind": "m", "expression": ["let", "    Source = #table({\"x\"}, {{1}})", "in", "    Source"]}]}})
        queries, _, _ = self.view(model)
        self.assertEqual([s.get("says") for s in queries["T"]["steps"]["items"]], ["Starts from the query Stage", "Removes 1 column: x"])
        self.assertEqual(queries["Stage"]["usedBy"], ["T"])

    # ---------------------------------------------------------------- pbi-tools extract folders
    def extract(self, legacy):
        root = self.tmp / "Extract"
        model = root / "Model"
        (model / "tables" / "Age").mkdir(parents=True)
        page = root / "Report" / "sections" / "000_Overview"
        page.mkdir(parents=True)
        (root / "Report" / "report.json").write_text(json.dumps({"id": 0}))
        (page / "section.json").write_text(json.dumps({"name": "page-1", "displayName": "Overview"}))
        if legacy:
            database = {"compatibilityLevel": 1465, "model": {"culture": "en-US"}}
            (model / "tables" / "Age" / "Age.json").write_text(json.dumps({"name": "Age", "columns": [{"name": "Age", "dataType": "int64"}],
                "partitions": [{"name": "Age-1", "mode": "import", "source": {"type": "query", "query": "SELECT * FROM [Age]", "dataSource": "ds-age"}}]}))
            (model / "dataSources" / "Age" / "mashup" / "Formulas").mkdir(parents=True)
            (model / "dataSources" / "Age" / "dataSource.json").write_text(json.dumps({
                "name": "ds-age", "connectionString": "Provider=Microsoft.PowerBI.OleDb;Global Pipe=7a;Mashup=;Location=Age"}))
            (model / "dataSources" / "Age" / "mashup" / "Formulas" / "Section1.m").write_text(
                "section Section1;\n\n" + SECTION.split("\n\n")[1] + "\n", encoding="utf-8")
        else:
            database = {"compatibilityLevel": 1550, "model": {
                "queryGroups": [{"folder": "Inputs", "annotations": [{"name": "PBI_QueryGroupOrder", "value": "0"}]}],
                "expressions": [{"name": "Folder", "kind": "m", "queryGroup": "Inputs"}],
                "annotations": [{"name": "PBI_QueryOrder", "value": '["Folder","Age"]'}]}}
            (model / "tables" / "Age" / "table.json").write_text(json.dumps({"name": "Age", "columns": [{"name": "Age", "dataType": "int64"}],
                "partitions": [{"name": "Age-1", "mode": "import", "source": {"type": "m"}}]}))
            (model / "queries").mkdir()
            members = legacy_mashup.members(SECTION)
            (model / "queries" / "Age.m").write_text(members["Age"], encoding="utf-8")
            (model / "queries" / "Folder.m").write_text(members["Folder"], encoding="utf-8")
        (model / "database.json").write_text(json.dumps(database), encoding="utf-8")
        return root

    def test_pbi_tools_extract_modern(self):
        root = self.extract(legacy=False)
        model, _ = load_extracted(root, self.tmp / "Extract.pbix", True)
        queries, view, _ = self.view(model)
        self.assertEqual([q["name"] for q in sorted(queries.values(), key=lambda q: q["order"])], ["Folder", "Age"])
        self.assertEqual((queries["Folder"]["group"], [g["folder"] for g in view["groups"]]), ("Inputs", ["Inputs"]))
        self.assertEqual([s["name"] for s in queries["Age"]["steps"]["items"]], ["Source", "Promoted Headers", "Changed Type"])
        self.assertEqual(queries["Folder"]["usedBy"], ["Age"])

    def test_pbi_tools_extract_legacy_package_as_a_folder_of_query_files(self):
        """What pbi-tools writes for a pre-2019 file: Mashup/Package/Formulas/Section1.m/<query>.m, one per query."""
        root = self.extract(legacy=True)
        only_tables, _ = load_extracted(root, self.tmp / "Extract.pbix", True)
        self.assertEqual(sorted(self.view(only_tables)[0]), ["Age"])            # before: the other queries were missing
        formulas = root / "Mashup" / "Package" / "Formulas" / "Section1.m"
        formulas.mkdir(parents=True)
        for chunk in SECTION.split("\n\n")[1:]:
            name = legacy_mashup._name(legacy_mashup._MEMBER.search(chunk).group(1))
            (formulas / f"{name}.m").write_text(chunk, encoding="utf-8")
        (root / "Mashup" / "Metadata").mkdir()
        (root / "Mashup" / "Metadata" / "metadata.json").write_text(json.dumps({
            "AllFormulas": {"Relationships": "AAAAAA=="},
            "Formulas": {"Section1/Age": {"IsPrivate": 0, "ResultType": "Table"}, "Section1/Folder": {"ResultType": "Text"},
                         "Section1/Not loaded": {"FillEnabled": 0, "FillObjectType": "ConnectionOnly", "ResultType": "Table"}}}))
        model, _ = load_extracted(root, self.tmp / "Extract.pbix", True)
        queries, view, _ = self.view(model)
        self.assertEqual(sorted(queries), ["Age", "Folder", "Not loaded", "Übersicht"])
        self.assertEqual((queries["Folder"]["kind"], queries["Folder"]["description"], queries["Folder"]["usedBy"]),
                         ("parameter", "Where the files are", ["Age"]))
        self.assertEqual((queries["Not loaded"]["load"], queries["Age"]["load"]), ("not loaded", "loaded"))
        self.assertEqual(queries["Not loaded"]["steps"]["items"][1]["says"], "Navigates to Orders")
        self.assertEqual(view["groups"], [])

    def test_pbi_tools_extract_legacy_package_as_one_file(self):
        root = self.extract(legacy=True)
        formulas = root / "Mashup" / "Package" / "Formulas"
        formulas.mkdir(parents=True)
        (formulas / "Section1.m").write_text(SECTION, encoding="utf-8")
        model, _ = load_extracted(root, self.tmp / "Extract.pbix", True)
        self.assertEqual(sorted(self.view(model)[0]), ["Age", "Folder", "Not loaded", "Übersicht"])

    # ---------------------------------------------------------------- thin reports
    def test_thin_report(self):
        """A report with a live connection carries no model, so it has no queries; the page still renders."""
        found = discover(SAMPLES / "thin-report-live-connection")
        self.assertIsNone(found["model"])
        report = parse_report(found["report"])
        payload = build_payload(None, report, None, "Thin")
        view = power_query_view(payload)
        self.assertEqual((view["queries"], view["groups"], view["recorded"]), ([], [], False))
        text = render_html(payload, self.tmp / "thin.html").read_text(encoding="utf-8")
        derived = json.JSONDecoder().raw_decode(text, text.index("const DERIVED = ") + len("const DERIVED = "))[0]
        self.assertEqual(derived["powerQuery"], {"recorded": False, "queries": [], "groups": []})

    # ---------------------------------------------------------------- shapes of M
    def test_shared_queries_partitions_several_tables_nested_m_and_no_let(self):
        nested = ('let\n    Source = let\n            Inner = Stage,\n            Filtered = Table.SelectRows(Inner, each [A] > 0)\n        in\n'
                  '            Filtered,\n    Typed = Table.TransformColumnTypes(Source, {{"A", Int64.Type}})\nin\n    Typed')
        model = self.bim({"model": {"name": "Shapes", "tables": [
            {"name": "Nested", "columns": [{"name": "A"}], "partitions": [{"name": "Nested", "source": {"type": "m", "expression": nested}}]},
            {"name": "Direct", "columns": [{"name": "A"}], "partitions": [{"name": "Direct", "source": {"type": "m", "expression": "Stage"}}]},
            {"name": "Call", "columns": [{"name": "A"}], "partitions": [{"name": "Call", "source": {"type": "m",
                "expression": 'Table.SelectRows(Stage, each [A] > Limit)'}}]},
            {"name": "Parts", "columns": [{"name": "A"}], "partitions": [
                {"name": "p1", "source": {"type": "m", "expression": 'let S = fnGet("one") in S'}},
                {"name": "p2", "source": {"type": "m", "expression": 'let S = fnGet("two") in S'}},
                {"name": "p3", "source": {"type": "calculated", "expression": "ROW(\"A\", 1)"}}]}],
            "expressions": [
                {"name": "Stage", "kind": "m", "expression": 'let Source = #table({"A"}, {{1}}) in Source'},
                {"name": "Limit", "kind": "m", "expression": '5 meta [IsParameterQuery=true, Type="Number"]'},
                {"name": "fnGet", "kind": "m", "expression": '(name as text) as table =>\nlet\n    Source = Stage,\n    Tagged = Table.AddColumn(Source, "Name", each name)\nin\n    Tagged'}]}})
        queries, _, _ = self.view(model)
        self.assertEqual(sorted(queries), ["Call", "Direct", "Limit", "Nested", "Parts / p1", "Parts / p2", "Stage", "fnGet"])
        # a query read by several tables, directly, through a function, and from inside a nested let
        self.assertEqual(queries["Stage"]["usedBy"], ["Call", "Direct", "Nested", "Parts"])
        self.assertEqual(queries["fnGet"]["usedBy"], ["Parts"])
        self.assertEqual((queries["fnGet"]["kind"], queries["fnGet"]["steps"]["scope"]), ("function", "function body"))
        # nested M: the inner let is one step's expression
        nested_steps = queries["Nested"]["steps"]["items"]
        self.assertEqual([s["name"] for s in nested_steps], ["Source", "Typed"])
        self.assertTrue(nested[nested_steps[0]["s"]:nested_steps[0]["e"]].startswith("let\n            Inner = Stage"))
        self.assertNotIn("says", nested_steps[0])
        self.assertEqual(nested_steps[1]["says"], "Sets the data type of 1 column: A (whole number)")
        # expressions without let
        for name in ("Direct", "Call", "Limit"):
            self.assertEqual((queries[name]["steps"]["status"], queries[name]["steps"]["items"], queries[name]["extraction"]["status"]),
                             ("none", [], "complete"), name)
        self.assertEqual(queries["Limit"]["kind"], "parameter")
        self.assertEqual(queries["Limit"]["usedBy"], ["Call"])
        # several partitions: one query per distinct text, each naming its partition; the DAX partition is not a query
        self.assertEqual((queries["Parts / p1"]["partitions"], queries["Parts / p2"]["partitions"]), (["p1"], ["p2"]))
        self.assertEqual(queries["Parts / p1"]["steps"]["items"][0]["says"], "Invokes the function fnGet")


if __name__ == "__main__":
    unittest.main()
