"""UI rework, Change 6: what the engine records for the seven-section document.

The versions beside the generation time, the authentication type of a data source (only when the file supplies
it, never an account), the data sources a partition reads through, and the rendered sections themselves.
"""
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pbidocgen  # noqa: E402
from pbidocgen import data_sources, input_limits, portable  # noqa: E402
from pbidocgen.model_parser import parse_model  # noqa: E402
from pbidocgen.renderer import build_payload, producer, render_html  # noqa: E402

ACCOUNTS = ("svc_marker_account", "marker_login", "marker_pw", "marker_key")


def doc():
    return {"model": {"name": "Sections", "dataSources": [
        {"name": "dw", "connectionString": "Provider=SQLNCLI11;Data Source=srv;Initial Catalog=db;Integrated Security=SSPI"},
        {"type": "structured", "name": "SQL/srv2;db2",
         "connectionDetails": {"protocol": "tds", "address": {"server": "srv2", "database": "db2"}},
         "credential": {"AuthenticationKind": "UsernamePassword", "Username": "svc_marker_account", "Password": "marker_pw"}}],
        "relationships": [
            {"name": "r1", "fromTable": "Sales", "fromColumn": "Amount", "toTable": "Calendar", "toColumn": "Date"},
            {"name": "r2", "fromTable": "Sales", "fromColumn": "Amount", "toTable": "LocalDateTable_1", "toColumn": "Date"}],
        "tables": [
            {"name": "Budget", "columns": [{"name": "Amount"}],
             "partitions": [{"name": "Budget", "source": {"query": "SELECT * FROM dbo.Budget", "dataSource": "dw"}}]},
            {"name": "Orders", "columns": [{"name": "Id"}], "partitions": [{"name": "Orders", "source": {
                "type": "m", "expression": 'let\n    S = #"SQL/srv2;db2",\n    T = S{[Schema="dbo",Item="Orders"]}[Data]\nin\n    T'}}]},
            {"name": "Sales", "columns": [{"name": "Amount"}, {"name": "Note"}, {"name": "Double", "type": "calculated", "expression": "[Amount] * 2"}],
             "measures": [{"name": "Total", "expression": "SUM(Sales[Amount])"}],
             "partitions": [{"name": "Sales", "source": {"type": "m", "expression": (
                 'let\n    S = Sql.Database("srv3", "db3"),\n    T = S{[Schema="dbo",Item="Sales"]}[Data],\n'
                 '    F = Table.SelectRows(T, each [Note] <> "SQL/srv2;db2")\nin\n    F')}}]},
            {"name": "Calendar", "columns": [{"name": "Date", "type": "calculatedTableColumn", "sourceColumn": "[Date]"}],
             "partitions": [{"name": "Calendar", "source": {"type": "calculated", "expression": "CALENDAR(DATE(2024,1,1), DATE(2024,12,31))"}}]},
            {"name": "LocalDateTable_1", "isHidden": True, "columns": [{"name": "Date", "type": "calculatedTableColumn", "sourceColumn": "[Date]"}],
             "annotations": [{"name": "__PBI_LocalDateTable", "value": "true"}],
             "partitions": [{"name": "LocalDateTable_1", "source": {"type": "calculated", "expression": "Calendar(Date(2020,1,1), Date(2020,12,31))"}}]}]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def model(self, document=None):
        path = self.tmp / "model.bim"
        path.write_text(json.dumps(document or doc()), encoding="utf-8")
        return parse_model(path)


class Producer(Base):
    def test_a_payload_says_which_engine_and_version_built_it(self):
        payload = build_payload(self.model(), None, None, "Sections")
        self.assertEqual(payload["producer"], {"engine": "pbi-doc-gen", "engineVersion": pbidocgen.__version__, "bidoc": None})
        self.assertRegex(pbidocgen.__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(producer("1.4.0")["bidoc"], "1.4.0")
        self.assertIsNone(producer("")["bidoc"])

    def test_the_command_line_writes_it_into_the_document(self):
        source = self.tmp / "model.bim"
        source.write_text(json.dumps(doc()), encoding="utf-8")
        out = self.tmp / "out.html"
        result = subprocess.run([sys.executable, "-m", "pbidocgen.cli", "--model", str(source), "--output", str(out)],
                                capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        html = out.read_text(encoding="utf-8")
        recorded = json.loads(re.search(r'"producer":\s*(\{[^{}]*\})', html).group(1))
        self.assertEqual(recorded, {"engine": "pbi-doc-gen", "engineVersion": pbidocgen.__version__, "bidoc": None})


class OtherOutputs(Base):
    def test_the_word_and_agent_outputs_state_the_versions_too(self):
        from pbidocgen.agent_writer import build_agent_md
        from pbidocgen.word_writer import render_docx
        payload = build_payload(self.model(), None, None, "Sections")
        payload["producer"]["bidoc"] = "1.2.3"
        self.assertIn(f"(bidoc 1.2.3 · pbi-doc-gen {pbidocgen.__version__})", build_agent_md(payload))
        docx = render_docx(payload, self.tmp / "s.docx")
        import zipfile
        with zipfile.ZipFile(docx) as z:
            body = z.read("word/document.xml").decode("utf-8")
        self.assertIn(f"bidoc 1.2.3 · pbi-doc-gen {pbidocgen.__version__}", body)
        del payload["producer"]
        self.assertIn("(bidoc not recorded · pbi-doc-gen not recorded)", build_agent_md(payload))


class Authentication(Base):
    def test_only_the_type_and_only_when_the_file_says(self):
        says = data_sources.authentication
        self.assertEqual(says({"connectionString": "Data Source=s;Integrated Security=SSPI"}), "Windows integrated security")
        self.assertEqual(says({"connectionString": "Data Source=s;Trusted_Connection=yes"}), "Windows integrated security")
        self.assertEqual(says({"connectionString": "Data Source=s;User ID=marker_login;Password=marker_pw"}), "User name and password")
        self.assertEqual(says({"connectionString": "Data Source=s", "signsInWithAccount": True}), "User name and password")
        self.assertEqual(says({"credential": {"AuthenticationKind": "OAuth2"}}), "OAuth2 (organisational account)")
        self.assertEqual(says({"credential": {"AuthenticationKind": "Key", "Key": "marker_key"}}), "Account key")
        self.assertEqual(says({"credential": {"AuthenticationKind": "Kerberos"}}), "Kerberos", "an unlisted kind is shown as the file names it")
        self.assertEqual(says({"impersonationMode": "impersonateServiceAccount"}), "Service account (impersonation)")
        self.assertEqual(says({"impersonationMode": 4}), "The current user (impersonation)")
        # nothing in the file: nothing is claimed
        for silent in ({}, {"connectionString": "Data Source=s;Initial Catalog=d"}, {"credential": "opaque"},
                       {"credential": {"Username": "marker_login"}}, {"impersonationMode": "default"}, {"impersonationMode": 1}):
            self.assertIsNone(says(silent), silent)
        # a value that is not a plain kind name is not repeated
        self.assertIsNone(says({"credential": {"AuthenticationKind": "x;Password=marker_pw"}}))
        self.assertIsNone(says({"credential": {"AuthenticationKind": "<script>"}}))

    def test_the_model_records_the_type_and_never_the_account(self):
        model = self.model()
        self.assertEqual({d["name"]: d["authentication"] for d in model["dataSources"]},
                         {"dw": "Windows integrated security", "SQL/srv2;db2": "User name and password"})
        html = render_html(build_payload(model, None, None, "Sections"), self.tmp / "s.html").read_text(encoding="utf-8")
        for account in ACCOUNTS:
            self.assertNotIn(account, html)
        self.assertNotIn("marker", json.dumps(model["dataSources"]))

    def test_a_partition_names_the_data_sources_it_reads_through(self):
        partitions = {t["name"]: t["partitions"][0] for t in self.model()["tables"]}
        self.assertEqual(partitions["Budget"]["dataSources"], ["dw"])
        self.assertEqual(partitions["Orders"]["dataSources"], ["SQL/srv2;db2"])
        # a query that connects by itself reads through no model data source, even when it mentions one as text
        self.assertNotIn("dataSources", partitions["Sales"])
        self.assertNotIn("dataSources", partitions["Calendar"])

    def test_a_pbix_or_abf_data_source(self):
        db = sqlite3.connect(":memory:")
        db.executescript('''
CREATE TABLE Model (ID, Name, DefaultMode, Culture);
CREATE TABLE "Table" (ID, Name, Description, IsHidden, DataCategory, LineageTag, SystemFlags, CalculationGroupID);
CREATE TABLE Column (ID, TableID, Type, SortByColumnID, ExplicitName, InferredName, ExplicitDataType, InferredDataType, Expression, SourceColumn);
CREATE TABLE Partition (ID, TableID, Name, Type, Mode, QueryDefinition, DataSourceID);
CREATE TABLE Measure (ID, TableID, Name, Expression, FormatString, IsHidden);
CREATE TABLE Annotation (ObjectID, Name, Value);
CREATE TABLE DataSource (ID, Name, Type, ConnectionString, ImpersonationMode, Credential, ConnectionDetails);
''')
        for statement in (
                "INSERT INTO Model VALUES (1, 'M', 0, 'en-US')",
                'INSERT INTO "Table" VALUES (10, \'Budget\', NULL, 0, NULL, NULL, 0, NULL)',
                "INSERT INTO Column VALUES (12, 10, 1, NULL, 'Amount', NULL, 6, 6, NULL, 'Amount')",
                "INSERT INTO Partition VALUES (30, 10, 'Budget', 1, 0, 'SELECT * FROM dbo.Budget', 40)",
                "INSERT INTO DataSource VALUES (40, 'dw', 1, 'Provider=SQLNCLI11;Data Source=srv;Initial Catalog=db;User ID=marker_login;Password=marker_pw', 2, NULL, NULL)",
                "INSERT INTO DataSource VALUES (41, 'lake', 2, NULL, 4, '{\"AuthenticationKind\":\"OAuth2\",\"Username\":\"svc_marker_account\"}', '{\"protocol\":\"tds\",\"address\":{\"server\":\"srv2\",\"database\":\"db2\"}}')",
                "INSERT INTO DataSource VALUES (42, 'opaque', 2, NULL, NULL, 'AQAAANCMnd8BFdERjHoAwE', '{\"protocol\":\"tds\",\"address\":{\"server\":\"srv3\",\"database\":\"db3\"}}')"):
            db.execute(statement)
        db.commit()
        raw = db.serialize()
        db.close()
        with mock.patch.object(portable, "_metadata", side_effect=lambda *a, **k: input_limits.open_metadata(raw)):
            document = portable.model_document("model.abf")
        self.assertNotIn("marker", json.dumps(document))
        model = self.model(document)
        self.assertEqual({d["name"]: d["authentication"] for d in model["dataSources"]},
                         {"dw": "User name and password", "lake": "OAuth2 (organisational account)", "opaque": None})
        self.assertEqual(model["tables"][0]["partitions"][0]["dataSources"], ["dw"])


class Rendered(Base):
    @unittest.skipUnless(shutil.which("node"), "Node needed to run the generated script")
    def test_sections_counts_and_data_source_controls(self):
        payload = build_payload(self.model(), None, None, "Sections")
        html = render_html(payload, self.tmp / "sections.html")
        script = Path(__file__).with_name("check_sections.cjs")
        result = subprocess.run(["node", str(script), str(html)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
