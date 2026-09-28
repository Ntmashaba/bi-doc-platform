"""shared-projection/1: code withheld by default, DAX and prose kept, cleaning best effort."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import DAX_TABLE, PBI_MARKERS, pbi_model  # noqa: E402

from bidoc_engines import power_bi  # noqa: E402
from bidoc_engines.projection import CODE_MARKER, clean_string, looks_like_code, project  # noqa: E402


class Classification(unittest.TestCase):
    def test_code(self):
        for text in ("SELECT MAX(x) AS m FROM etl.Watermark", "select count(*) from t", "select Region from dbo.Sales",
                     "DELETE FROM dbo.FactSales WHERE d > 1", "EXEC dbo.usp_Load", "exec usp_LoadFact",
                     "insert into dbo.T (a) values (1)", "TRUNCATE TABLE stage.Orders", "let S = 1 in S",
                     'Excel.Workbook(File.Contents("x.xlsx"))', 'Sql.Database("s","d")', '#"Changed Type"'):
            self.assertTrue(looks_like_code(text), text)

    def test_not_code(self):
        for text in ("Select a value from the slicer", "Delete from the list when done", "Execute the pipeline daily",
                     "SUM(Sales[Amount])", DAX_TABLE, "CALENDARAUTO()", "Generated date table (CALENDAR/CALENDARAUTO)",
                     "http://services.odata.org/V3/Northwind/Northwind.svc/", r"C:\Data\Sales.xlsx",
                     "See Finance.Policy (v2) for details"):
            self.assertFalse(looks_like_code(text), text)

    def test_cleaning(self):
        cleaned, reasons = clean_string('Server=x;Password=Secr3t;Uid=a https://h/p?sig=ABC&x=1 '
                                        'Table.FromRows({{"Alice", 1}}, {"N","V"}) Binary.FromText("AAAA")')
        for secret in ("Secr3t", "sig=ABC", "Alice", '"AAAA"'):
            self.assertNotIn(secret, cleaned)
        self.assertIn("x=1", cleaned)
        self.assertEqual(sorted(reasons), ["credential", "entered_data", "secret_bearing_url"])


class SeededPowerBI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d:
            cls.payload = power_bi.load(pbi_model(Path(d)), "bim")

    def text(self, data):
        return json.dumps(data, ensure_ascii=False)

    def test_withheld_removes_every_marker(self):
        data, omissions = project("power_bi", self.payload, query_code="withheld")
        text = self.text(data) + self.text(omissions)
        for name, marker in PBI_MARKERS.items():
            self.assertNotIn(marker, text, name)
        self.assertEqual(data["model"]["sourcePath"], "")
        self.assertTrue(any(o["reason"] == "machine_path" for o in omissions))

    def test_included_keeps_code_but_cleans_secrets_and_data(self):
        data, omissions = project("power_bi", self.payload, query_code="included")
        text = self.text(data)
        for name in ("odbc_password", "web_token", "entered_row", "entered_base64"):
            self.assertNotIn(PBI_MARKERS[name], text, name)
        self.assertIn(PBI_MARKERS["sql_literal"], text)          # code shared as written, by choice
        self.assertIn("Sql.Database", text)
        self.assertFalse(any(o["reason"] == "query_code_withheld" for o in omissions))

    def test_dax_and_prose_survive(self):
        data, _ = project("power_bi", self.payload, query_code="withheld")
        text = self.text(data)
        self.assertIn(DAX_TABLE, text)
        self.assertIn("SUM(Sales[Amount])", text)
        self.assertIn("Select a value from the slicer", text)
        self.assertIn(CODE_MARKER, text)

    def test_omissions_never_echo_values(self):
        _, omissions = project("power_bi", self.payload, query_code="withheld")
        self.assertTrue(omissions)
        for o in omissions:
            self.assertEqual(set(o), {"path", "reason", "effect"})
            self.assertTrue(o["path"].startswith("/"))

    def test_input_is_not_modified(self):
        before = self.text(self.payload)
        project("power_bi", self.payload, query_code="withheld")
        self.assertEqual(self.text(self.payload), before)


class AdfDetails(unittest.TestCase):
    def test_sql_built_in_expressions_is_code(self):
        self.assertTrue(looks_like_code("@concat('SELECT COUNT(1) AS cnt FROM ', variables('schema'))"))
        self.assertFalse(looks_like_code("@concat('Hello ', pipeline().parameters.name)"))
        self.assertFalse(looks_like_code("@pipeline().parameters.selectMode"))

    def test_dataflow_query_options_withheld_rest_kept(self):
        config = ("(allowSchemaDrift: true, isolationLevel: 'READ_UNCOMMITTED', "
                  "query: (concat('select max(', $key, ') as m from ', $table)), format: 'query')")
        data, omissions = project("adf", {"dataflows": [{"steps": [{"config": config}]}]})
        kept = data["dataflows"][0]["steps"][0]["config"]
        self.assertNotIn("select max", kept)
        self.assertIn("isolationLevel: 'READ_UNCOMMITTED'", kept)
        self.assertIn(f"query: '{CODE_MARKER}'", kept)
        self.assertEqual(omissions[0]["path"], "/dataflows/0/steps/0/config")
        included, _ = project("adf", {"dataflows": [{"steps": [{"config": config}]}]}, query_code="included")
        self.assertIn("select max", included["dataflows"][0]["steps"][0]["config"])

    def test_label_kept_code_withheld(self):
        payload = {"pipelines": [{"activities": [{"detail": "AzureSqlSource → AzureSqlSink | pre-copy: DELETE FROM dbo.F WHERE d > 1"}]}]}
        data, omissions = project("adf", payload)
        self.assertEqual(data["pipelines"][0]["activities"][0]["detail"],
                         f"AzureSqlSource → AzureSqlSink | pre-copy: {CODE_MARKER}")
        self.assertEqual(omissions[0]["reason"], "query_code_withheld")


if __name__ == "__main__":
    unittest.main()
