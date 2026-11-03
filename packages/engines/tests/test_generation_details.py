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
