"""A document above the publication limit is still written locally; only publication is refused."""
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import adf_factory, pbi_model  # noqa: E402

from bidoc_contracts import ContractError, Limits, validate_artifact  # noqa: E402
from bidoc_engines import generate as gen  # noqa: E402
from bidoc_engines.envelope import GENERATION_LIMITS, publication_problem  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402

MIB = 1024 * 1024


class Oversize(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.model = pbi_model(self.tmp / "m")

    def run_gen(self, out="out", profile="local"):
        return generate(GenerateRequest(engine="power_bi", source_path=str(self.model), source_kind="bim",
                                        output_dir=str(self.tmp / out), profile=profile))

    def test_exactly_at_and_one_over_the_publication_limit(self):
        base = self.run_gen("a")
        size = Path(base.artifact_path).stat().st_size
        self.assertEqual(base.status, "completed")
        with mock.patch.object(gen, "PUBLICATION_LIMITS", replace(Limits(), html_bytes=size)):
            at = self.run_gen("b")
        self.assertEqual(at.status, "completed", at.warnings)
        self.assertIsNone(at.publication)
        with mock.patch.object(gen, "PUBLICATION_LIMITS", replace(Limits(), html_bytes=size - 1)):
            over = self.run_gen("c")
        self.assertEqual(over.status, "local_only")
        self.assertEqual(over.publication["code"], "ARTIFACT_TOO_LARGE")
        self.assertEqual((over.publication["size_bytes"], over.publication["limit_bytes"]), (size, size - 1))
        self.assertTrue(Path(over.artifact_path).is_file())            # kept, not discarded
        validate_artifact(Path(over.artifact_path).read_bytes(), limits=GENERATION_LIMITS)
        self.assertIn("MiB", over.warnings[0])
        self.assertEqual(set(over.publication["breakdown_bytes"]),
                         {"document", "manifest", "model_payload_in_manifest", "section_text"})

    def test_real_document_over_25_mib_is_written_with_a_clear_reason(self):
        data = json.loads(self.model.read_text(encoding="utf-8"))
        data["model"]["tables"][0]["partitions"][0]["source"]["expression"] += "\n// " + "x" * (13 * MIB)
        self.model.write_text(json.dumps(data), encoding="utf-8")
        res = self.run_gen()
        self.assertEqual(res.status, "local_only", res.errors)
        size = Path(res.artifact_path).stat().st_size
        self.assertGreater(size, 25 * MIB)
        self.assertEqual(res.publication["limit_bytes"], 25 * MIB)
        self.assertEqual(res.publication["operation"], "publish")
        self.assertIn("usable locally", res.warnings[0])
        self.assertEqual(res.errors, [])
        art = Path(res.artifact_path).read_bytes()
        validate_artifact(art, limits=GENERATION_LIMITS)                # structurally valid
        self.assertEqual(publication_problem(art)["code"], "ARTIFACT_TOO_LARGE")
        with self.assertRaises(ContractError):                          # the strict default still refuses it
            validate_artifact(art)

    def test_manifest_limit_is_named_with_its_own_size_limit_and_remedy(self):
        base = self.run_gen("m0")
        html = Path(base.artifact_path).stat().st_size
        with mock.patch.object(gen, "PUBLICATION_LIMITS", replace(Limits(), manifest_bytes=1000)):
            res = self.run_gen("m1")
        self.assertEqual(res.status, "local_only")
        pub = res.publication
        self.assertEqual((pub["code"], pub["category"], pub["limit_bytes"]), ("MANIFEST_TOO_LARGE", "manifest", 1000))
        self.assertLess(pub["size_bytes"], html)                          # the manifest, not the whole document
        self.assertGreater(pub["size_bytes"], 1000)
        self.assertIn("MAX_MANIFEST_BYTES", res.warnings[0])
        self.assertNotIn("MAX_HTML_BYTES", res.warnings[0])

    def test_incomplete_input_above_the_generation_ceiling_is_refused_not_written(self):
        factory = adf_factory(self.tmp / "factory")
        (factory / "pipeline" / "broken.json").write_text("{not json", encoding="utf-8")

        def run_adf(out):
            return generate(GenerateRequest(engine="adf", source_path=str(factory), source_kind="adf_git",
                                            output_dir=str(self.tmp / out)))
        self.assertEqual(run_adf("i0").status, "local_only")             # incomplete input: written, no manifest
        with mock.patch.object(gen, "GENERATION_LIMITS", replace(Limits(), html_bytes=1000)):
            res = run_adf("i1")
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.errors[0]["code"], "CONTRACT_VIOLATION")
        self.assertIn("generation ceiling", res.errors[0]["message"])
        self.assertFalse(list((self.tmp / "i1").glob("*.html")) if (self.tmp / "i1").exists() else [])

    def test_generation_ceiling_is_deliberate(self):
        self.assertGreater(GENERATION_LIMITS.html_bytes, Limits().html_bytes)
        with mock.patch("bidoc_engines.envelope.GENERATION_LIMITS", replace(Limits(), html_bytes=1000)):
            res = self.run_gen("ceiling")
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.errors[0]["code"], "CONTRACT_VIOLATION")
        self.assertIn("ARTIFACT_TOO_LARGE", res.errors[0]["message"])


if __name__ == "__main__":
    unittest.main()
