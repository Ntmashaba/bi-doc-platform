"""Details a source cannot give, recorded at generation, before the document is sealed.

Power BI: report location, catalogue folder and connection account references (pbi-doc-gen's --metadata).
Data Factory: environment, owner, factory, source location, runbook, folder and notes (adf-doc-gen's --details).
They come from GenerateRequest.details_path or, without one, from <source name>.bidoc-details.json beside the
source, so every regeneration records them again. The page shows them read-only; the artifact stays valid.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import adf_factory, pbi_model  # noqa: E402
from adfdocgen.details import read_details  # noqa: E402
from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.generate import DETAILS_SIDECAR, GenerateRequest, details_sidecar, generate  # noqa: E402
from pbidocgen.catalog import read_metadata  # noqa: E402

PBI_DETAILS = {"reportLocation": "https://app.powerbi.com/groups/finance/reports/sales", "folder": "Finance / Monthly",
               "connections": [{"sourceType": "SQL Server", "server": "sql.example.local", "database": "SalesDW",
                                "username": "svc-pbi-refresh", "authentication": "Windows"}]}
ADF_DETAILS = {"environment": "Production", "owner": "Data Platform", "runbook": "https://wiki.example.com/adf-sales",
               "sourceLocation": "https://dev.azure.com/contoso/data/_git/adf-sales (main)", "folder": "Sales"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.model = pbi_model(self.tmp / "model")
        self.factory = adf_factory(self.tmp / "factory")

    def write(self, name, value):
        path = self.tmp / name
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def run_pbi(self, **kw):
        return generate(GenerateRequest(engine="power_bi", source_path=str(self.model), source_kind="bim",
                                        output_dir=str(self.tmp / kw.pop("out", "out")), **kw))

    def run_adf(self, **kw):
        return generate(GenerateRequest(engine="adf", source_path=str(self.factory), source_kind="adf_git",
                                        output_dir=str(self.tmp / kw.pop("out", "out")), **kw))

    def sealed(self, result):
        self.assertEqual(result.status, "completed", result.errors)
        data = Path(result.artifact_path).read_bytes()
        manifest = validate_artifact(data)                     # recorded before sealing, so the hash holds
        return data.decode("utf-8"), manifest


class Recorded(Base):
    def test_power_bi_details_from_a_named_file_in_both_profiles(self):
        details = self.write("sales.json", PBI_DETAILS)
        for profile in ("local", "shared"):
            with self.subTest(profile=profile):
                html, manifest = self.sealed(self.run_pbi(details_path=str(details), profile=profile, out=profile))
                expected = dict(PBI_DETAILS, connections=[dict(PBI_DETAILS["connections"][0], connectionName="")])
                self.assertEqual(read_metadata(html), expected)
                self.assertEqual(manifest["native_payload"]["data"]["documentation"], expected)

    def test_data_factory_details_from_the_file_beside_the_source(self):
        sidecar = details_sidecar(self.factory)
        self.assertEqual(sidecar, self.tmp / f"factory{DETAILS_SIDECAR}")
        sidecar.write_text(json.dumps(ADF_DETAILS), encoding="utf-8")
        html, _ = self.sealed(self.run_adf())
        self.assertEqual({k: v for k, v in read_details(html).items() if v}, ADF_DETAILS)

    def test_a_named_file_wins_over_the_one_beside_the_source(self):
        details_sidecar(self.factory).write_text(json.dumps({"owner": "Beside the source"}), encoding="utf-8")
        named = self.write("named.json", {"owner": "Named on the request"})
        html, _ = self.sealed(self.run_adf(details_path=str(named)))
        self.assertEqual(read_details(html)["owner"], "Named on the request")

    def test_regeneration_records_the_same_details_under_the_same_identity(self):
        details_sidecar(self.model).write_text(json.dumps(PBI_DETAILS), encoding="utf-8")
        first, again = self.run_pbi(), self.run_pbi()
        self.assertEqual(first.document_id, again.document_id)
        self.assertEqual(read_metadata(self.sealed(first)[0]), read_metadata(self.sealed(again)[0]))
        self.assertEqual(read_metadata(self.sealed(again)[0])["folder"], "Finance / Monthly")

    def test_details_do_not_change_the_document_identity(self):
        without = self.run_adf()
        details_sidecar(self.factory).write_text(json.dumps(ADF_DETAILS), encoding="utf-8")
        self.assertEqual(self.run_adf().document_id, without.document_id)

    def test_without_details_the_document_is_as_before(self):
        html, _ = self.sealed(self.run_adf())
        self.assertEqual(set(read_details(html).values()), {""})
        html, _ = self.sealed(self.run_pbi())
        self.assertEqual((read_metadata(html)["reportLocation"], read_metadata(html)["folder"]), ("", ""))

    def test_a_shared_copy_withholds_a_personal_location_and_a_local_copy_keeps_it(self):
        details = self.write("personal.json", {"reportLocation": r"C:\Users\nt\Reports\Sales.pbix"})
        local, _ = self.sealed(self.run_pbi(details_path=str(details), out="local"))
        shared, _ = self.sealed(self.run_pbi(details_path=str(details), profile="shared", out="shared"))
        self.assertEqual(read_metadata(local)["reportLocation"], r"C:\Users\nt\Reports\Sales.pbix")
        self.assertIn("Sales.pbix — personal location withheld [ref ", read_metadata(shared)["reportLocation"])
        self.assertNotIn(r"C:\Users\nt", shared)


# One credential policy for both engines (pbidocgen and adfdocgen keep identical copies of credential_values.py).
CREDENTIALS = ["Password=hunter2", "Server=sql;Database=dw;pwd=hunter2", "client_secret=examplevalue", "api_key=abc123",
               "x-api-key=abc123", "AccountKey=YWJjZGVm==", "SharedAccessSignature=sv=2021-08-06", "token=abc123",
               "https://x.example.com/report?token=abc123", "https://store.example.com/c?sv=2021&sig=abc123",
               "https://user:hunter2@x.example.com/r", "Authorization: Basic dXNlcjpwYXNz", "Authorization: Bearer abc123",
               "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9"]
REFERENCES = ["svc-pbi-refresh", "Windows", "OAuth2 (password stored in Key Vault)",
              "https://kv.vault.azure.net/secrets/sql-password", "@Microsoft.KeyVault(SecretUri=https://kv.vault.azure.net/secrets/x)",
              "https://user@dev.azure.com/contoso/data/_git/adf-sales", "Bearer authentication", "Basic authentication",
              "Rotate the client secret quarterly", "Key Vault secret: sql-pwd", "Token-based access via managed identity"]


class Credentials(Base):
    def test_both_engines_keep_the_same_policy(self):
        import adfdocgen.credential_values as adf_copy
        import pbidocgen.credential_values as pbi_copy
        self.assertEqual(Path(adf_copy.__file__).read_bytes(), Path(pbi_copy.__file__).read_bytes(),
                         "pbidocgen and adfdocgen credential_values.py must stay identical")
        from adfdocgen.details import validate_details
        from pbidocgen.catalog import validate_metadata
        for value in CREDENTIALS:
            for check, details in ((validate_details, {"notes": value}), (validate_details, {"runbook": value}),
                                   (validate_metadata, {"reportLocation": value}),
                                   (validate_metadata, {"connections": [{"server": "s", "authentication": value}]})):
                with self.subTest(value=value, field=next(iter(details))), self.assertRaises(ValueError) as caught:
                    check(details)
                self.assertIn("secret", str(caught.exception))
        for value in REFERENCES:
            with self.subTest(value=value):
                self.assertEqual(validate_details({"notes": value})["notes"], value)
                self.assertEqual(validate_metadata({"connections": [{"username": value}]})["connections"][0]["username"], value)

    def test_a_credential_stops_generation_in_both_profiles_without_echoing_it(self):
        for engine, details in (("power_bi", {"connections": [{"server": "s", "authentication": "client_secret=s3cr3tvalue"}]}),
                                ("adf", {"notes": "client_secret=s3cr3tvalue"})):
            for profile in ("local", "shared"):
                with self.subTest(engine=engine, profile=profile):
                    path = self.write(f"{engine}-{profile}.json", details)
                    run = self.run_pbi if engine == "power_bi" else self.run_adf
                    result = run(details_path=str(path), profile=profile, out=f"refused-{engine}-{profile}")
                    self.assertEqual((result.status, result.errors[0]["code"]), ("failed", "INVALID_INPUT"))
                    self.assertIn(path.name, result.errors[0]["message"])
                    self.assertNotIn("s3cr3tvalue", result.errors[0]["message"])
                    self.assertFalse((self.tmp / f"refused-{engine}-{profile}").exists() and
                                     any((self.tmp / f"refused-{engine}-{profile}").glob("*.html")))


class Environment(Base):
    """Factory details' `environment` is for readers; --environment files the document and is part of its identity."""

    def note(self, result):
        return [w for w in result.warnings if "environment" in w and DETAILS_SIDECAR in w]

    def test_details_alone_say_the_document_is_filed_with_no_environment(self):
        plain = self.run_adf()
        details_sidecar(self.factory).write_text(json.dumps({"environment": "Production"}), encoding="utf-8")
        result = self.run_adf()
        self.assertEqual(result.document_id, plain.document_id, "the details never change the identity")
        self.assertEqual(self.note(result), [f"factory{DETAILS_SIDECAR} gives the environment 'Production' for readers "
                                             "of the factory details, but the document is filed with no environment. Use "
                                             "--environment 'Production' to file it there; the environment is part of the "
                                             "document's identity."])
        self.assertEqual(read_details(self.sealed(result)[0])["environment"], "Production")

    def test_a_conflict_is_named_and_the_filing_environment_decides_identity(self):
        filed = self.run_adf(environment="Test")
        details_sidecar(self.factory).write_text(json.dumps({"environment": "Production"}), encoding="utf-8")
        result = self.run_adf(environment="Test")
        self.assertEqual(result.document_id, filed.document_id)
        self.assertIn("filed under 'Test' (--environment)", self.note(result)[0])

    def test_agreement_or_no_environment_in_the_details_needs_no_note(self):
        details_sidecar(self.factory).write_text(json.dumps({"environment": "production", "owner": "x"}), encoding="utf-8")
        self.assertEqual(self.note(self.run_adf(environment="Production")), [])
        details_sidecar(self.factory).write_text(json.dumps({"owner": "x"}), encoding="utf-8")
        self.assertEqual(self.note(self.run_adf()), [])
        details_sidecar(self.model).write_text(json.dumps(PBI_DETAILS), encoding="utf-8")
        self.assertEqual(self.note(self.run_pbi()), [])


class Refused(Base):
    def assert_refused(self, result, *phrases):
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.errors[0]["code"], "INVALID_INPUT")
        for phrase in phrases:
            self.assertIn(phrase, result.errors[0]["message"])
        self.assertFalse((self.tmp / "out").exists() and any((self.tmp / "out").glob("*.html")), "nothing is written")

    def test_a_secret_is_refused_by_either_engine(self):
        self.assert_refused(self.run_pbi(details_path=str(self.write("s.json", {"reportLocation": "https://x.example.com/r?token=abc123"}))),
                            "s.json", "secret")
        self.assert_refused(self.run_adf(details_path=str(self.write("a.json", {"runbook": "https://x.example.com/?sig=abc123"}))),
                            "a.json", "secret")

    def test_fields_of_the_other_engine_or_none_are_refused(self):
        self.assert_refused(self.run_pbi(details_path=str(self.write("w.json", ADF_DETAILS))), "w.json", "Unsupported")
        self.assert_refused(self.run_adf(details_path=str(self.write("p.json", PBI_DETAILS))), "p.json", "Unsupported")
        self.assert_refused(self.run_adf(details_path=str(self.write("e.json", "null"))), "e.json", "empty")
        self.assert_refused(self.run_pbi(details_path=str(self.write("n.json", "null"))), "n.json", "must be an object")

    def test_unreadable_or_missing_files_are_refused(self):
        self.assert_refused(self.run_adf(details_path=str(self.write("bad.json", "{oops"))), "bad.json", "JSON")
        self.assert_refused(self.run_adf(details_path=str(self.tmp / "missing.json")), "not found")
        details_sidecar(self.factory).write_text("not json", encoding="utf-8")
        self.assert_refused(self.run_adf(), f"factory{DETAILS_SIDECAR}", "JSON")   # a broken file beside it is not ignored


if __name__ == "__main__":
    unittest.main()
