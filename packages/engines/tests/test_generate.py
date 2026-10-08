"""generate(): valid envelope-v1 artifacts from both engines, stream identity, and the
local/shared policies end to end (A29 across HTML, manifest and search text)."""
import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import ADF_MARKERS, MEASURE_TAG, PBI_MARKERS, SALES_TAG, adf_factory, pbi_model  # noqa: E402

from bidoc_contracts import locate_manifest, validate_artifact  # noqa: E402
from bidoc_engines import adf, power_bi  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = self.tmp / "out"

    def run_adf(self, source, **kw):
        return generate(GenerateRequest(engine="adf", source_path=str(source), source_kind="adf_git",
                                        output_dir=str(self.out), **kw))

    def run_pbi(self, source, **kw):
        return generate(GenerateRequest(engine="power_bi", source_path=str(source), source_kind="bim",
                                        output_dir=str(self.out), **kw))

    def artifact(self, result, view_ids):
        self.assertEqual(result.status, "completed", result.errors)
        data = Path(result.artifact_path).read_bytes()
        return data, validate_artifact(data, view_ids=view_ids)


class AdfGeneration(Base):
    def setUp(self):
        super().setUp()
        self.factory = adf_factory(self.tmp / "factory")

    def test_local_keeps_everything_shared_withholds_code(self):
        local, lm = self.artifact(self.run_adf(self.factory), adf.VIEW_IDS)
        shared, sm = self.artifact(self.run_adf(self.factory, profile="shared"), adf.VIEW_IDS)
        self.assertEqual(lm["projection"]["profile"], "local")
        self.assertIn(ADF_MARKERS["sql_literal"].encode(), local)       # local documentation is complete
        for name, marker in ADF_MARKERS.items():
            self.assertNotIn(marker.encode(), shared, name)
        self.assertEqual(sm["projection"]["options"], {"query_code": "withheld"})
        self.assertEqual(lm["document_id"], sm["document_id"])             # one stream, two revisions
        self.assertNotEqual(lm["revision_id"], sm["revision_id"])

    def test_shared_with_code_included_is_cleaned_across_the_whole_artifact(self):
        """HTML, embedded payload and manifest: code as written, supported credential patterns cleaned."""
        local, _ = self.artifact(self.run_adf(self.factory), adf.VIEW_IDS)
        self.assertIn(ADF_MARKERS["script_api_key"].encode(), local)    # local output is the engine's own, unchanged
        shared, m = self.artifact(self.run_adf(self.factory, profile="shared", query_code="included"), adf.VIEW_IDS)
        for name in ("sql_literal", "precopy_literal"):
            self.assertIn(ADF_MARKERS[name].encode(), shared, name)
        for name in ("inline_password", "sas_signature", "url_password", "bearer_token", "script_api_key"):
            self.assertNotIn(ADF_MARKERS[name].encode(), shared, name)
        self.assertIn("credential", {o["reason"] for o in m["projection"]["omissions"]})

    def test_objects_bindings_and_search_text(self):
        _, m = self.artifact(self.run_adf(self.factory), adf.VIEW_IDS)
        by_id = {o["object_id"]: o for o in m["objects"]}
        copy = by_id["adf:activity:PL_Load/Copy daily"]
        self.assertEqual(copy["parent_object_id"], "adf:pipeline:PL_Load")
        ops = {(b["operation"], b["endpoint"]["object"] or b["endpoint"]["path"]) for b in copy["bindings"]}
        self.assertIn(("write", "Sales"), ops)
        self.assertIn(("read", "sales/daily"), ops)
        purge = by_id["adf:activity:PL_Load/Purge"]
        self.assertEqual({b["operation"] for b in purge["bindings"]}, {"delete"})
        write = next(b for b in copy["bindings"] if b["operation"] == "write")
        self.assertEqual(write["normalized_endpoint"]["port"], 1433)
        self.assertEqual(m["publication"]["scope_key"], "factory")
        text = " ".join(s["title"] + " " + s["text"] for s in m["sections"])
        self.assertIn("Loads daily sales.", text)

    def test_sidecar_is_beside_the_source_and_not_read_as_input(self):
        first = self.run_adf(self.factory)
        self.assertTrue((self.tmp / "factory.bidoc-identity.json").is_file())
        self.assertFalse(any(self.factory.glob("*.bidoc-identity.json")))
        again = self.run_adf(self.factory)
        self.assertEqual(first.document_id, again.document_id)
        self.assertFalse(any("not recognised" in w for w in again.warnings))

    def test_environment_is_part_of_the_stream(self):
        prod = self.run_adf(self.factory, environment="Production")
        prod2 = self.run_adf(self.factory, environment="prod")          # alias of the same key
        test = self.run_adf(self.factory, environment="Test")
        self.assertEqual(prod.document_id, prod2.document_id)
        self.assertNotEqual(prod.document_id, test.document_id)

    def test_copied_source_needs_an_explicit_identity_choice(self):
        original = self.run_adf(self.factory)
        copy = self.tmp / "copy"
        shutil.copytree(self.factory, copy)
        shutil.copy(self.tmp / "factory.bidoc-identity.json", self.tmp / "copy.bidoc-identity.json")
        refused = self.run_adf(copy)
        self.assertEqual(refused.errors[0]["code"], "IDENTITY_DECISION_REQUIRED")
        self.assertIsNone(refused.artifact_path)
        self.assertEqual(self.run_adf(copy, identity_choice="existing").document_id, original.document_id)
        self.assertNotEqual(self.run_adf(copy, identity_choice="new").document_id, original.document_id)

    def test_selection_is_its_own_stream(self):
        single = self.tmp / "single"
        single.mkdir()
        shutil.copy(self.factory / "pipeline" / "PL_Load.json", single / "PL_Load.json")
        result = generate(GenerateRequest(engine="adf", source_path=str(single), source_kind="adf_resources",
                                          output_dir=str(self.out)))
        _, m = self.artifact(result, adf.VIEW_IDS)
        self.assertTrue(m["publication"]["scope_key"].startswith("selection-"))
        self.assertEqual(m["publication"]["scope_descriptor"]["resources"], ["pipeline/PL_Load"])
        self.assertTrue(all(o["coverage"] == "selection" for o in m["objects"]))

    def test_incomplete_read_is_local_only(self):
        (self.factory / "pipeline" / "broken.json").write_text("{not json", encoding="utf-8")
        local = self.run_adf(self.factory)
        self.assertEqual(local.status, "local_only")
        with self.assertRaises(Exception):
            locate_manifest(Path(local.artifact_path).read_bytes())
        self.assertEqual(self.run_adf(self.factory, profile="shared").status, "local_only")

    def test_incomplete_documents_with_the_same_title_do_not_share_an_output_file(self):
        copy = self.factory.parent / "elsewhere" / self.factory.name                    # same name, different folder
        shutil.copytree(self.factory, copy)
        for folder in (self.factory, copy):
            (folder / "pipeline" / "broken.json").write_text("{not json", encoding="utf-8")
        first, second = self.run_adf(self.factory), self.run_adf(copy)
        self.assertEqual((first.status, second.status), ("local_only", "local_only"))
        self.assertNotEqual(first.artifact_path, second.artifact_path)                 # neither overwrote the other
        self.assertTrue(Path(first.artifact_path).is_file() and Path(second.artifact_path).is_file())
        self.assertTrue(first.artifact_path.endswith(".local.html"))
        again = self.run_adf(self.factory)                                              # a rerun refreshes its own file
        self.assertEqual(again.artifact_path, first.artifact_path)
        shared = self.run_adf(self.factory, profile="shared")
        self.assertTrue(shared.artifact_path.endswith(".shared.local.html"))
        self.assertNotEqual(shared.artifact_path, first.artifact_path)

    def test_cancellation(self):
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(generate(GenerateRequest(engine="adf", source_path=str(self.factory), source_kind="adf_git",
                                                  output_dir=str(self.out)), None, cancel).status, "cancelled")
        self.assertFalse(self.out.exists() and any(self.out.glob("*.html")))


class PowerBIGeneration(Base):
    def setUp(self):
        super().setUp()
        self.model = pbi_model(self.tmp / "model")

    def test_a29_every_representation(self):
        shared, m = self.artifact(self.run_pbi(self.model, profile="shared"), power_bi.VIEW_IDS)
        search = json.dumps(m["sections"], ensure_ascii=False)
        for name, marker in PBI_MARKERS.items():
            self.assertNotIn(marker.encode(), shared, name)              # HTML, DATA and manifest
            self.assertNotIn(marker, search, name)
        self.assertNotIn(str(self.tmp).encode(), shared)                 # no machine path
        local, _ = self.artifact(self.run_pbi(self.model), power_bi.VIEW_IDS)
        self.assertIn(PBI_MARKERS["sql_literal"].encode(), local)

    def test_shared_with_code_included_is_cleaned_across_the_whole_artifact(self):
        """HTML, embedded payload, search index and manifest: code as written, credential patterns cleaned."""
        shared, m = self.artifact(self.run_pbi(self.model, profile="shared", query_code="included"), power_bi.VIEW_IDS)
        for name in ("sql_literal", "piped_literal"):
            self.assertIn(PBI_MARKERS[name].encode(), shared, name)
        for name in ("odbc_password", "web_token", "entered_row", "entered_base64", "url_password", "bearer_token",
                     "api_key"):
            self.assertNotIn(PBI_MARKERS[name].encode(), shared, name)
        self.assertNotIn(str(self.tmp).encode(), shared)
        reasons = {o["reason"] for o in m["projection"]["omissions"]}
        self.assertLessEqual({"credential", "secret_bearing_url", "entered_data"}, reasons)

    def test_local_output_is_not_projected(self):
        """The local profile publishes the engine's payload as read: every seeded value, no omissions."""
        local, m = self.artifact(self.run_pbi(self.model), power_bi.VIEW_IDS)
        for name, marker in PBI_MARKERS.items():
            self.assertIn(marker.encode(), local, name)
        self.assertEqual((m["projection"]["profile"], m["projection"]["omissions"]), ("local", []))

    def test_lineage_tags_are_object_ids_and_sources_are_logical(self):
        _, m = self.artifact(self.run_pbi(self.model), power_bi.VIEW_IDS)
        ids = {o["object_id"]: o for o in m["objects"]}
        self.assertIn(f"pbi:table:{SALES_TAG}", ids)
        self.assertEqual(ids[f"pbi:measure:{MEASURE_TAG}"]["parent_object_id"], f"pbi:table:{SALES_TAG}")
        self.assertIn("pbi:table:name:Dates", ids)                        # fallback without a tag
        sources = [o for o in m["objects"] if o["kind"] == "source"]
        fact = next(o for o in sources if o["label"].endswith("dbo.FactSales"))
        ep = fact["bindings"][0]["endpoint"]
        self.assertEqual((ep["server"], ep["port"], ep["database"]), ("finance-sql.corp.local", 1444, "FinanceDW"))
        self.assertEqual(fact["bindings"][0]["operation"], "read")

    def test_the_document_and_the_manifest_use_the_same_object_ids(self):
        """Hub links carry manifest object ids; the document's own index resolves the same ids (pbi-identity/1)."""
        for profile in ("local", "shared"):
            with self.subTest(profile=profile):
                data, m = self.artifact(self.run_pbi(self.model, profile=profile), power_bi.VIEW_IDS)
                text = data.decode("utf-8")
                start = re.search(r"\bconst DERIVED = ", text).end()
                index = json.JSONDecoder().raw_decode(text, start)[0]["search"]
                indexed = {item[3]: (index["kinds"][item[0]], item[1]) for item in index["items"]}
                for obj in m["objects"]:
                    if obj["kind"] in ("table", "measure"):
                        self.assertEqual(indexed[obj["object_id"]][1], obj["label"], obj["object_id"])
                self.assertEqual(indexed[f"pbi:table:{SALES_TAG}"], ("table", "Sales"))
                self.assertEqual(indexed[f"pbi:measure:{MEASURE_TAG}"], ("measure", "Revenue"))
                self.assertEqual(indexed["pbi:table:name:Dates"], ("calculated table", "Dates"))
                self.assertEqual(indexed["pbi:column:name:Sales/Amount"], ("column", "Amount"))
                self.assertEqual(indexed["pbi:query:name:Native"], ("query", "Native"))
                if profile == "shared":      # built from the projected payload: nothing withheld is searchable
                    blob = json.dumps(index, ensure_ascii=False)
                    for name, marker in PBI_MARKERS.items():
                        self.assertNotIn(marker, blob, name)
                    self.assertNotIn(str(self.tmp), blob)

    def test_include_query_code_is_explicit(self):
        _, m = self.artifact(self.run_pbi(self.model, profile="shared", query_code="included"), power_bi.VIEW_IDS)
        self.assertEqual(m["projection"]["options"], {"query_code": "included"})
        text = json.dumps(m["native_payload"]["data"])
        self.assertIn("Sql.Database", text)
        self.assertNotIn(PBI_MARKERS["odbc_password"], text)

    def test_scope_follows_engine_modes(self):
        # the engine's own mode names (combined / semantic-only / report-only)
        _, m = self.artifact(self.run_pbi(self.model), power_bi.VIEW_IDS)
        self.assertEqual(m["publication"]["scope_key"], "model")
        self.assertEqual(m["native_payload"]["data"]["mode"], "semantic-only")

    def test_invalid_input(self):
        r = self.run_pbi(self.tmp / "missing.bim")
        self.assertEqual((r.status, r.errors[0]["code"]), ("failed", "INVALID_INPUT"))
        r = generate(GenerateRequest(engine="power_bi", source_path=str(self.model), source_kind="adf_git",
                                     output_dir=str(self.out)))
        self.assertEqual(r.errors[0]["code"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()


class LiveConnectedPbix(Base):
    """A thin PBIX (no DataModel) reads its model from Analysis Services."""

    def make_pbix(self):
        import zipfile
        pbix = self.tmp / "thin.pbix"
        with zipfile.ZipFile(pbix, "w") as z:
            z.writestr("Connections", json.dumps({"Connections": [{"ConnectionString":
                       "Data Source=asazure://uks.asazure.windows.net/srv;Initial Catalog=Sales;Password=x"}]}))
            z.writestr("Report/definition/report.json", "{}")
            z.writestr("Report/definition/pages/p1/page.json", json.dumps({"name": "p1", "displayName": "P1"}))
        (self.tmp / "extract").mkdir()
        return pbix

    def test_thin_pbix_generates_with_live_source(self):
        pbix = self.make_pbix()
        result = generate(GenerateRequest(engine="power_bi", source_path=str(pbix), source_kind="pbix",
                                          extracted_path=str(self.tmp / "extract"), output_dir=str(self.out),
                                          profile="shared"))
        data, artifact = self.artifact(result, ["pbi.overview", "pbi.source", "pbi.page"])
        text = data.decode("utf-8", "replace")
        self.assertIn("asazure://uks.asazure.windows.net/srv", text)
        self.assertNotIn("Password", text)
