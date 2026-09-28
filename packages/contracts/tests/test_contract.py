"""Envelope v1 contract: schema, validator, hash rule and golden fixtures."""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from bidoc_contracts import (PLACEHOLDER, ContractError, Limits, content_sha256, embed_manifest,  # noqa: E402
                             load_schema, locate_manifest, validate_artifact, validate_manifest)
import build_fixtures  # noqa: E402

FIX = HERE / "fixtures"
GOLDEN = {  # byte-level golden hashes; change only with a deliberate contract change
    "valid-power-bi.html": "77f869e760896c11e2c0faf99e4e0524092562e4598531bdc5136a4f1bc77b12",
    "valid-adf-crlf.html": "0c4ae6e86917098720adec2f5f7af9038278794342b4e72eb0c57ff054dc397e",
    "valid-escaped-script-text.html": "cbfbba6971e392721085e4439bc8245aeb2baabaaa74c8335fd0b675b06daa02",
}
INVALID = {
    "invalid-unsupported-version.html": "UNSUPPORTED_SCHEMA_VERSION",
    "invalid-duplicate-manifest.html": "MANIFEST_DUPLICATE",
    "invalid-hash-mismatch.html": "HASH_MISMATCH",
    "invalid-missing-anchor.html": "MISSING_ANCHOR",
}


def code_of(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except ContractError as exc:
        return exc.code
    return None


class FixtureTests(unittest.TestCase):
    def test_fixtures_are_reproducible(self):
        subprocess.run([sys.executable, str(HERE / "build_fixtures.py"), "--check"], check=True)

    def test_valid_fixtures_and_golden_hashes(self):
        for name, digest in GOLDEN.items():
            data = (FIX / name).read_bytes()
            manifest = validate_artifact(data)
            self.assertEqual(manifest["content_sha256"], digest, name)
            self.assertEqual(content_sha256(data), digest, name)

    def test_hash_rule_is_sha256_of_placeholder_substitution(self):
        data = (FIX / "valid-power-bi.html").read_bytes()
        loc = locate_manifest(data)
        expected = hashlib.sha256(data[:loc.start] + b"<!--PBIDOC-MANIFEST-->" + data[loc.end:]).hexdigest()
        self.assertEqual(content_sha256(data), expected)

    def test_crlf_bytes_are_preserved_by_the_hash(self):
        data = (FIX / "valid-adf-crlf.html").read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertEqual(code_of(validate_artifact, data.replace(b"\r\n", b"\n")), "HASH_MISMATCH")

    def test_invalid_fixtures(self):
        for name, code in INVALID.items():
            self.assertEqual(code_of(validate_artifact, (FIX / name).read_bytes()), code, name)

    def test_script_like_text_is_escaped_and_round_trips(self):
        data = (FIX / "valid-escaped-script-text.html").read_bytes()
        body = locate_manifest(data).body
        self.assertNotIn(b"<", body)
        manifest = validate_artifact(data)
        self.assertIn('</script><script id="pbidoc-manifest">', manifest["description"])
        self.assertIn("</SCRIPT >", manifest["sections"][0]["text"])


class ManifestRuleTests(unittest.TestCase):
    def setUp(self):
        self.m = build_fixtures.power_bi()
        self.m["content_sha256"] = "a" * 64

    def assertCode(self, code, **kw):
        self.assertEqual(code_of(validate_manifest, self.m, **kw), code)

    def test_fixture_manifest_is_valid(self):
        validate_manifest(self.m)
        validate_manifest(build_fixtures.adf() | {"content_sha256": "b" * 64})

    def test_unknown_top_level_field_rejected(self):
        self.m["extra"] = 1
        self.assertCode("SCHEMA_VIOLATION")

    def test_boolean_is_not_schema_version_1(self):
        self.m["schema_version"] = True
        self.assertCode("UNSUPPORTED_SCHEMA_VERSION")

    def test_required_v12_fields(self):
        for field in ("publication", "projection", "navigation", "identity_version", "classification", "objects"):
            m = copy.deepcopy(self.m)
            del m[field]
            self.assertEqual(code_of(validate_manifest, m), "SCHEMA_VIOLATION", field)

    def test_field_formats(self):
        cases = [("document_id", "0F8B5C2E-6A1D-4C3E-9B7A-1D2E3F405061"), ("title", " padded"), ("title", ""),
                 ("generated_at", "2026-09-28 12:00 UTC"), ("generated_at", "2026-09-28T12:00:00+02:00"),
                 ("tags", ["a", "a"]), ("tags", ["x" * 51]), ("content_sha256", "A" * 64)]
        for field, value in cases:
            m = copy.deepcopy(self.m)
            m[field] = value
            self.assertEqual(code_of(validate_manifest, m), "SCHEMA_VIOLATION", (field, value))

    def test_snapshot_must_be_complete(self):
        self.m["publication"]["snapshot_state"] = "partial"
        self.assertCode("SCHEMA_VIOLATION")

    def test_query_code_policy_is_explicit(self):
        del self.m["projection"]["options"]["query_code"]
        self.assertCode("SCHEMA_VIOLATION")
        self.m["projection"]["options"]["query_code"] = "cleaned"
        self.assertCode("SCHEMA_VIOLATION")

    def test_port_range(self):
        self.m["objects"][2]["bindings"][0]["endpoint"]["port"] = 70000
        self.assertCode("SCHEMA_VIOLATION")

    def test_cross_references(self):
        cases = [
            lambda m: m["objects"][1].update(section_id="nowhere"),
            lambda m: m["objects"][1].update(parent_object_id="pbi:table:missing"),
            lambda m: m["objects"].append(copy.deepcopy(m["objects"][0])),
            lambda m: m["sections"].append(copy.deepcopy(m["sections"][0])),
            lambda m: m["navigation"]["targets"][0].update(target_id="pbi:measure:missing"),
            lambda m: m["objects"][2]["bindings"][0].update(evidence_refs=["/sourceObjects/9"]),
            lambda m: m.update(assets=[{"path": "assets/a.png", "sha256": "c" * 64}]),
        ]
        for i, mutate in enumerate(cases):
            m = copy.deepcopy(self.m)
            mutate(m)
            self.assertEqual(code_of(validate_manifest, m), "CONTRACT_VIOLATION", i)

    def test_view_registry(self):
        self.assertCode("CONTRACT_VIOLATION", view_ids={"pbi.tab"})
        validate_manifest(self.m, view_ids={"pbi.tab", "pbi.measure"})

    def test_section_text_limit(self):
        self.assertCode("CONTRACT_VIOLATION", limits=Limits(section_text_bytes=10))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.valid = (FIX / "valid-power-bi.html").read_bytes()

    def test_missing_manifest(self):
        self.assertEqual(code_of(validate_artifact, b"<html></html>"), "MANIFEST_MISSING")

    def test_manifest_must_be_inert(self):
        data = self.valid.replace(b'<script type="application/json" id="pbidoc-manifest">',
                                  b'<script id="pbidoc-manifest">')
        self.assertEqual(code_of(validate_artifact, data), "MANIFEST_NOT_INERT")

    def test_tag_spelling_is_outside_the_hash(self):
        # The whole element, opening tag included, is replaced before hashing.
        data = self.valid.replace(b'<script type="application/json" id="pbidoc-manifest">',
                                  b"<SCRIPT id='pbidoc-manifest' type=application/json>")
        self.assertEqual(validate_artifact(data)["content_sha256"], GOLDEN["valid-power-bi.html"])

    def test_size_limits(self):
        self.assertEqual(code_of(validate_artifact, self.valid, limits=Limits(html_bytes=100)), "ARTIFACT_TOO_LARGE")
        self.assertEqual(code_of(validate_artifact, self.valid, limits=Limits(manifest_bytes=100)), "MANIFEST_TOO_LARGE")

    def test_strict_json(self):
        body = locate_manifest(self.valid).body
        for bad in (b'{"a":1,"a":2}', b'{"a":NaN}', b"not json", b"\xff"):
            data = self.valid.replace(body, bad)
            self.assertEqual(code_of(validate_artifact, data), "MANIFEST_INVALID_JSON", bad)

    def test_embed_requires_one_placeholder(self):
        m = build_fixtures.power_bi()
        with self.assertRaises(ValueError):
            embed_manifest(b"<html></html>", m)
        with self.assertRaises(ValueError):
            embed_manifest(PLACEHOLDER * 2, m)
        with self.assertRaises(ValueError):
            embed_manifest(self.valid + PLACEHOLDER, m)


@unittest.skipUnless(importlib.util.find_spec("jsonschema"), "install the dev extra: pip install -e .[dev]")
class ReferenceSchemaTests(unittest.TestCase):
    """The bundled interpreter and the reference jsonschema library agree."""

    def test_schema_is_valid_draft_2020_12(self):
        import jsonschema
        jsonschema.Draft202012Validator.check_schema(load_schema())

    def test_agreement_on_fixtures_and_mutations(self):
        import jsonschema
        ref = jsonschema.Draft202012Validator(load_schema())
        samples = [build_fixtures.power_bi(), build_fixtures.adf()]
        for field, value in [("title", " x"), ("schema_version", 2), ("tags", ["a", "a"]), ("extra", 1)]:
            m = build_fixtures.power_bi()
            m[field] = value
            samples.append(m)
        m = build_fixtures.power_bi()
        m["objects"][2]["bindings"][0]["endpoint"]["port"] = 0
        samples.append(m)
        for m in samples:
            ours = code_of(validate_manifest, m)
            ours_schema_ok = ours not in ("SCHEMA_VIOLATION", "UNSUPPORTED_SCHEMA_VERSION")
            self.assertEqual(ref.is_valid(m), ours_schema_ok, json.dumps(m)[:200])


if __name__ == "__main__":
    unittest.main()
