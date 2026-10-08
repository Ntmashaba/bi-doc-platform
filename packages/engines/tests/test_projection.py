"""shared-projection/1: code withheld by default, DAX and prose kept, cleaning best effort."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import ADF_MARKERS, DAX_TABLE, PBI_MARKERS, adf_factory, pbi_model  # noqa: E402

from bidoc_engines import adf, power_bi  # noqa: E402
from bidoc_engines.projection import (CODE_MARKER, CREDENTIAL_MARKER, clean_string, looks_like_code,  # noqa: E402
                                      project)

X = CREDENTIAL_MARKER


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


class Markers(unittest.TestCase):
    def test_the_document_recognises_exactly_what_the_projection_writes(self):
        """pbi-doc-gen reports a script as withheld or cleaned by these texts (pbidocgen/publication.py)."""
        from bidoc_engines import projection
        from pbidocgen import publication
        self.assertEqual(publication.CODE_WITHHELD, projection.CODE_MARKER)
        self.assertIn(projection.CREDENTIAL_MARKER, publication.CLEANED)
        self.assertIn(projection.DATA_MARKER, publication.CLEANED)
        path = projection.withhold_personal_paths(r"C:\Users\a\b.xlsx")
        self.assertTrue(any(marker in path for marker in publication.CLEANED), path)
        self.assertEqual(publication.publication_of(projection.CODE_MARKER), "withheld")
        self.assertEqual(publication.publication_of(f'Sql.Database("s", [Password="{projection.CREDENTIAL_MARKER}"])'), "cleaned")
        self.assertEqual(publication.publication_of('let S = 1 in S'), "included")


class Credentials(unittest.TestCase):
    """The supported credential patterns, each as it is written in M, JSON, header text and URLs."""

    def cleaned(self, text, reason=None):
        new, reasons = clean_string(text)
        if reason:
            self.assertIn(reason, reasons, text)
        return new

    def test_user_and_password_in_a_url(self):
        for text, expected in (
                ('Web.Contents("https://svc:Secr3t@api.contoso.com/v1/orders?top=5")',
                 f'Web.Contents("https://{X}@api.contoso.com/v1/orders?top=5")'),
                ("ftp://reader:p%40ss@files.contoso.com/out/", f"ftp://{X}@files.contoso.com/out/"),
                ("https://ghp_0123456789abcdef@github.com/org/repo.git", f"https://{X}@github.com/org/repo.git"),
                ("postgresql://svc:p@ss:w0rd@db.contoso.com:5432/dw", f"postgresql://{X}@db.contoso.com:5432/dw"),
                ("see https://a:b@one.example/x and sftp://c:d@two.example/y",
                 f"see https://{X}@one.example/x and sftp://{X}@two.example/y")):
            self.assertEqual(self.cleaned(text, "secret_bearing_url"), expected)

    def test_a_url_without_user_information_is_unchanged(self):
        for text in ("https://api.contoso.com/v1/orders?email=first.last@contoso.com",
                     "https://contoso.sharepoint.com/sites/Finance/Shared Documents/Budget@2024.xlsx",
                     "abfss://raw@lake.dfs.core.windows.net/sales",      # container@account is an address
                     "wasbs://landing@acct.blob.core.windows.net/in/", "abfs://raw@lake.dfs.core.windows.net",
                     "mailto:data.team@contoso.com", "first.last@contoso.com", r"\\server\share\a@b.csv",
                     "https://api.contoso.com/users/@me/items"):
            self.assertEqual(clean_string(text), (text, []), text)

    def test_authorization_headers(self):
        for text, expected in (
                ('[Headers=[Authorization="Bearer abc.DEF-123"]]', f'[Headers=[Authorization="{X}"]]'),
                ('[Headers=[#"Authorization"="Basic dXNlcjpwYXNz", Accept="application/json"]]',
                 f'[Headers=[#"Authorization"="{X}", Accept="application/json"]]'),
                ('[Headers=[#"x-api-key"="k-0123456789"]]', f'[Headers=[#"x-api-key"="{X}"]]'),
                ('{"Authorization": "Bearer abc.DEF-123", "Accept": "*/*"}', f'{{"Authorization": "{X}", "Accept": "*/*"}}'),
                ('{"Ocp-Apim-Subscription-Key": "0123456789abcdef"}', f'{{"Ocp-Apim-Subscription-Key": "{X}"}}'),
                ("Authorization: Bearer abc.DEF-123", f"Authorization: {X}"),
                ("curl -H 'Authorization: Basic dXNlcjpwYXNz' https://h/", f"curl -H 'Authorization: {X}' https://h/"),
                ("Proxy-Authorization: Basic dXNlcjpwYXNz", f"Proxy-Authorization: {X}"),
                ('Headers=[ApiKey = "0123456789abcdef"]', f'Headers=[ApiKey = "{X}"]'),
                ('[Headers=[Authorization="Bearer " & "eyJhbGciOiJIUzI1NiJ9.e30.abc"]]',
                 f'[Headers=[Authorization="Bearer " & "{X}"]]'),
                ("token is Bearer eyJhbGciOiJIUzI1NiJ9.e30.abc today", f"token is Bearer {X} today")):
            self.assertEqual(self.cleaned(text), expected, text)
        self.assertEqual(clean_string('Authorization="Bearer abc.DEF-123"')[1], ["credential"])

    def test_names_of_values_held_elsewhere_are_kept(self):
        for text in ('[Headers=[Authorization="Bearer " & Token]]',                # the value comes from a query
                     "[Headers=[Authorization=AuthHeader]]",
                     '[Headers=[#"x-api-key"=ApiKeyParameter]]',
                     "Authorization: @{activity('Get token').output.access_token}",  # a Data Factory expression
                     "Authorization=@pipeline().parameters.auth",
                     "Uses bearer authentication against the warehouse API.",
                     "The API key rotation runbook is owned by Platform.",
                     "Authorization is granted by the Finance data owner.",
                     "Bearer tokens are issued by Entra ID."):
            self.assertEqual(clean_string(text), (text, []), text)

    def test_cleaning_again_changes_nothing(self):
        once = ('Server=x;Password=Secr3t;Pwd="a b";AccountKey=abc==;https://h/p?sig=ABC&code=1 '
                'https://u:p@h.example/x Authorization="Bearer abc.DEF-123" #"x-api-key"="k-0123456789" '
                "Authorization: Bearer abc.DEF-123 | Bearer eyJhbGciOiJIUzI1NiJ9.e30.abc "
                'Table.FromRows({{"Alice", 1}}, {"N","V"}) Binary.FromText("AAAA")')
        first, reasons = clean_string(once)
        self.assertEqual(sorted(reasons), ["credential", "entered_data", "secret_bearing_url"])
        for secret in ("Secr3t", "a b", "abc==", "sig=ABC", "u:p@", "abc.DEF-123", "k-0123456789", "eyJhbGci", "Alice"):
            self.assertNotIn(secret, first, secret)
        self.assertEqual(clean_string(first), (first, []))


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
        for name in ("odbc_password", "web_token", "entered_row", "entered_base64", "url_password", "bearer_token",
                     "api_key"):
            self.assertNotIn(PBI_MARKERS[name], text, name)
        for name in ("sql_literal", "piped_literal", "step_name", "described_column"):   # code shared as written, by choice
            self.assertIn(PBI_MARKERS[name], text, name)
        self.assertIn("Sql.Database", text)
        self.assertIn(f'https://{X}@api.contoso.com/v1/orders', text)          # the address stays readable
        self.assertIn(f'Authorization=\\"{X}\\", #\\"x-api-key\\"=\\"{X}\\"', text)
        self.assertFalse(any(o["reason"] == "query_code_withheld" for o in omissions))
        reasons = {o["reason"] for o in omissions}
        self.assertLessEqual({"credential", "secret_bearing_url", "entered_data"}, reasons)

    def test_withheld_code_goes_whole(self):
        """A " | " inside a literal is not a boundary: nothing after it is left behind."""
        data, _ = project("power_bi", self.payload, query_code="withheld")
        text = self.text(data)
        self.assertNotIn("SEEDPIPE", text)
        self.assertNotIn("Table.SelectRows", text)
        row = next(i for i, q in enumerate(self.payload["sourceQueries"]) if q["queryName"] == "Piped")
        self.assertIn(PBI_MARKERS["piped_literal"], self.payload["sourceQueries"][row]["mCode"])
        self.assertEqual(data["sourceQueries"][row]["mCode"], CODE_MARKER)
        for holder in (data["tableSources"], data["sourceObjects"], data["model"]["tables"]):
            self.assertNotIn(" | ", self.text([h for h in holder if "Piped" in self.text(h)]).replace(CODE_MARKER, ""))

    def test_projecting_again_changes_nothing(self):
        """The library re-projects stored payloads: a cleaned payload must be a fixed point."""
        for mode in ("withheld", "included"):
            once, _ = project("power_bi", self.payload, query_code=mode)
            twice, omissions = project("power_bi", once, query_code=mode)
            self.assertEqual(self.text(twice), self.text(once), mode)
            self.assertFalse([o for o in omissions if o["reason"] in ("credential", "secret_bearing_url", "entered_data")])

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

    def test_a_separator_inside_a_literal_stays_withheld(self):
        detail = "AzureSqlSource → AzureSqlSink | query: SELECT a FROM dbo.T WHERE s = 'A | SEEDTAIL_1' AND b = 2 | retries: 3"
        data, _ = project("adf", {"pipelines": [{"activities": [{"detail": detail}]}]})
        self.assertEqual(data["pipelines"][0]["activities"][0]["detail"],
                         f"AzureSqlSource → AzureSqlSink | query: {CODE_MARKER} | retries: 3")

    def test_credentials_in_any_string_are_cleaned_when_code_is_included(self):
        payload = {"pipelines": [{"activities": [
            {"detail": "POST https://svc:Secr3t@hooks.contoso.com/v1/loaded | header Authorization: Bearer abc.DEF-123",
             "script": "EXEC dbo.usp_Call @headers = N'{\"x-api-key\": \"k-0123456789\"}'",
             "description": "Calls ftp://reader:hunter2@files.contoso.com/out/ with Authorization: Basic dXNlcjpwYXNz"}]}]}
        for mode in ("withheld", "included"):
            data, omissions = project("adf", payload, query_code=mode)
            text = json.dumps(data, ensure_ascii=False)
            for secret in ("Secr3t", "abc.DEF-123", "hunter2", "dXNlcjpwYXNz") + (("k-0123456789",) if mode == "included" else ()):
                self.assertNotIn(secret, text, (mode, secret))
            self.assertNotIn("k-0123456789", text)
            self.assertIn("hooks.contoso.com/v1/loaded", text)
            again, _ = project("adf", data, query_code=mode)
            self.assertEqual(again, data, mode)
        self.assertIn("EXEC dbo.usp_Call", text)


class SeededDataFactory(unittest.TestCase):
    """The same policy through the Data Factory engine's own payload."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d:
            cls.payload = adf.load(adf_factory(Path(d)), "adf_git")

    def test_local_payload_is_the_engines_own(self):
        text = json.dumps(self.payload, ensure_ascii=False)
        for name in ("sql_literal", "precopy_literal", "script_api_key"):        # code is complete locally
            self.assertIn(ADF_MARKERS[name], text, name)
        for name in ("inline_password", "sas_signature", "url_password", "bearer_token"):   # the engine's own redaction
            self.assertNotIn(ADF_MARKERS[name], text, name)

    def test_withheld_removes_every_marker(self):
        data, omissions = project("adf", self.payload, query_code="withheld")
        text = json.dumps(data, ensure_ascii=False) + json.dumps(omissions)
        for name, marker in ADF_MARKERS.items():
            self.assertNotIn(marker, text, name)

    def test_included_keeps_code_and_cleans_credentials(self):
        data, omissions = project("adf", self.payload, query_code="included")
        text = json.dumps(data, ensure_ascii=False)
        for name in ("sql_literal", "precopy_literal"):
            self.assertIn(ADF_MARKERS[name], text, name)
        for name in ("inline_password", "sas_signature", "url_password", "bearer_token", "script_api_key"):
            self.assertNotIn(ADF_MARKERS[name], text, name)
        self.assertIn("sp_invoke_external_rest_endpoint", text)
        self.assertIn("credential", {o["reason"] for o in omissions})
        again, _ = project("adf", data, query_code="included")
        self.assertEqual(again, data)


if __name__ == "__main__":
    unittest.main()
