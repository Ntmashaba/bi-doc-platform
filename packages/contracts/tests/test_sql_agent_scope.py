"""SQL Agent selection scope, key hashing and the envelope schema enums (v4 section 5.2)."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from bidoc_contracts import ContractError, ScopeError, scope_key, sql_agent_scope, validate_manifest  # noqa: E402
import build_fixtures  # noqa: E402

GUID_A = "guid:1b2c3d4e-0000-4000-8000-000000000001"
GUID_B = "guid:1b2c3d4e-0000-4000-8000-000000000002"
NAME_A = "name:nightly load"


def code_of(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except ContractError as exc:
        return exc.code
    return None


class SqlAgentScopeTests(unittest.TestCase):
    def test_selection_is_canonical_sorted_distinct(self):
        descriptor = sql_agent_scope([NAME_A, GUID_A, NAME_A])
        self.assertEqual(descriptor, {"kind": "selection", "jobs": [GUID_A, NAME_A]})

    def test_key_is_hashed_and_order_independent(self):
        key = scope_key("sql_agent", sql_agent_scope([NAME_A, GUID_A]))
        self.assertRegex(key, r"^selection-[0-9a-f]{16}$")
        self.assertEqual(key, scope_key("sql_agent", sql_agent_scope([GUID_A, NAME_A])))

    def test_different_selections_are_different_streams(self):
        self.assertNotEqual(scope_key("sql_agent", sql_agent_scope([GUID_A])),
                            scope_key("sql_agent", sql_agent_scope([GUID_B])))
        self.assertNotEqual(scope_key("sql_agent", sql_agent_scope([GUID_A])),
                            scope_key("sql_agent", sql_agent_scope([GUID_A, NAME_A])))

    def test_identity_must_be_guid_or_name_prefixed(self):
        for bad in ("Nightly Load", "guid:", "name:", "other:x", ""):
            with self.assertRaises(ScopeError, msg=bad):
                sql_agent_scope([bad])

    def test_selection_must_name_a_job(self):
        with self.assertRaises(ScopeError):
            sql_agent_scope([])

    def test_non_canonical_selection_rejected(self):
        with self.assertRaises(ScopeError):
            scope_key("sql_agent", {"kind": "selection", "jobs": [NAME_A, GUID_A]})
        with self.assertRaises(ScopeError):
            scope_key("sql_agent", {"kind": "selection", "jobs": [GUID_A, GUID_A]})

    def test_wrong_kind_and_shape_rejected(self):
        with self.assertRaises(ScopeError):
            scope_key("sql_agent", {"kind": "factory"})
        with self.assertRaises(ScopeError):
            scope_key("sql_agent", {"kind": "selection", "resources": [NAME_A]})
        with self.assertRaises(ScopeError):
            scope_key("sql_agent", {"kind": "selection"})


class SqlAgentSchemaEnumTests(unittest.TestCase):
    """document_type 'sql_agent' and source kinds 'agent_script' / 'agent_folder' validate."""

    def manifest(self):
        m = build_fixtures.adf()
        m["document_type"] = "sql_agent"
        m["publication"]["scope_descriptor"] = sql_agent_scope([GUID_A, NAME_A])
        m["publication"]["scope_key"] = scope_key("sql_agent", m["publication"]["scope_descriptor"])
        m["content_sha256"] = "a" * 64
        return m

    def test_agent_script_source(self):
        m = self.manifest()
        m["source"]["kind"] = "agent_script"
        validate_manifest(m)

    def test_agent_folder_source(self):
        m = self.manifest()
        m["source"]["kind"] = "agent_folder"
        validate_manifest(m)

    def test_unknown_source_kind_still_rejected(self):
        m = self.manifest()
        m["source"]["kind"] = "agent_sql"
        self.assertEqual(code_of(validate_manifest, m), "SCHEMA_VIOLATION")

    def test_scope_key_must_match_descriptor(self):
        m = self.manifest()
        m["publication"]["scope_key"] = "selection-" + "0" * 16
        self.assertEqual(code_of(validate_manifest, m), "CONTRACT_VIOLATION")
        m = self.manifest()
        m["publication"]["scope_descriptor"] = {"kind": "selection", "jobs": [NAME_A, GUID_A]}
        self.assertEqual(code_of(validate_manifest, m), "CONTRACT_VIOLATION")


if __name__ == "__main__":
    unittest.main()
