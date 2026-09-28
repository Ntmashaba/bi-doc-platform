"""Import-time re-projection keeps identity and never restores withheld code."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import ADF_MARKERS, adf_factory  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.convert import UnsupportedProjection, reproject  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402


class Reproject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        factory = adf_factory(cls.tmp / "factory")
        cls.manifests = {}
        for profile in ("local", "shared"):
            r = generate(GenerateRequest(engine="adf", source_path=str(factory), source_kind="adf_git",
                                         output_dir=str(cls.tmp / "out"), profile=profile))
            cls.manifests[profile] = validate_artifact(Path(r.artifact_path).read_bytes())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_local_artifact_becomes_shared_with_identity_kept(self):
        local = self.manifests["local"]
        stored, manifest = reproject(local)
        validate_artifact(stored)
        for key in ("document_id", "revision_id", "title", "generated_at", "publication", "source", "classification"):
            self.assertEqual(manifest[key], local[key], key)
        self.assertEqual((manifest["projection"]["profile"], manifest["projection"]["options"]["query_code"]),
                         ("shared", "withheld"))
        self.assertNotIn(ADF_MARKERS["sql_literal"].encode(), stored)
        self.assertNotEqual(manifest["content_sha256"], local["content_sha256"])

    def test_published_copy_is_marked_read_only(self):
        # A37: the library copy carries the published flag the engine templates honour.
        stored, manifest = reproject(self.manifests["local"])
        self.assertTrue(manifest["native_payload"]["data"]["published"])
        self.assertNotIn(b'"published": true', b"") ; self.assertIn(b'"published":true', stored.replace(b" ", b""))
        self.assertIn(b"published, read-only copy", stored)

    def test_withheld_code_is_never_restored(self):
        shared = self.manifests["shared"]
        _, manifest = reproject(shared, query_code="included")
        self.assertEqual(manifest["projection"]["options"]["query_code"], "withheld")
        prior = {(o["path"], o["reason"]) for o in shared["projection"]["omissions"]}
        self.assertTrue(prior <= {(o["path"], o["reason"]) for o in manifest["projection"]["omissions"]})

    def test_unsupported_native_schema(self):
        m = dict(self.manifests["local"], projection=dict(self.manifests["local"]["projection"], native_schema="adf-doc-gen/9"))
        with self.assertRaises(UnsupportedProjection):
            reproject(m)


if __name__ == "__main__":
    unittest.main()
