"""rel-rules/1 (handoff A20, A21, A22)."""
import unittest

from bidoc_engines.endpoints import endpoint, normalize
from bidoc_relationships import Document, detect

SQL = dict(system="Azure SQL Database", server="sql1.corp.local", database="DW", schema="dbo", object="Sales")
LAKE = dict(system="Azure Data Lake Storage Gen2", storage_account="lake", container="raw", path="sales/daily")


def binding(op, ep, resolution="static", coverage="complete", bid=None):
    e = endpoint(**ep)
    return {"binding_id": bid or f"{op}:{sorted(ep.items())}", "operation": op, "endpoint": e,
            "normalized_endpoint": normalize(e), "normalization_version": "endpoint-norm/1",
            "invocation_context": {}, "evidence_refs": [], "resolution": resolution, "coverage": coverage}


def adf(*bindings, env="production", doc="a" * 8):
    return Document(doc, "r-" + doc, "adf", env, (
        {"object_id": "adf:pipeline:PL", "kind": "pipeline", "parent_object_id": None, "bindings": []},
        {"object_id": "adf:activity:PL/Copy", "kind": "activity", "parent_object_id": "adf:pipeline:PL",
         "bindings": list(bindings)}))


def pbi(ep, env="production", resolution="static", doc="p" * 8):
    return Document(doc, "r-" + doc, "power_bi", env, (
        {"object_id": "pbi:source:x", "kind": "source", "parent_object_id": None,
         "bindings": [binding("read", ep, resolution)]},))


def one(docs):
    found = detect(docs)
    return [(r["kind"], r["confidence"]) for r in found]


class Rules(unittest.TestCase):
    def test_exact_static_producer(self):                                       # A20
        found = detect([adf(binding("write", SQL)), pbi(SQL)])
        self.assertEqual([(r["kind"], r["confidence"]) for r in found], [("produces", "exact_static")])
        r = found[0]
        self.assertEqual((r["source_object_id"], r["source_parent_object_id"], r["target_object_id"]),
                         ("adf:activity:PL/Copy", "adf:pipeline:PL", "pbi:source:x"))
        self.assertEqual(r["evidence"]["source_endpoint"]["server"], "sql1.corp.local")

    def test_default_port_equals_no_port(self):
        self.assertEqual(one([adf(binding("write", dict(SQL, port=1433))), pbi(SQL)]), [("produces", "exact_static")])

    def test_contradictions_never_join(self):                                   # A21, A30
        for other in (dict(SQL, server="sql2.corp.local"), dict(SQL, port=1444), dict(SQL, database="Archive"),
                      dict(SQL, instance="REPORTING"), dict(SQL, object="Orders")):
            with self.subTest(other=other):
                self.assertEqual(one([adf(binding("write", SQL)), pbi(other)]), [])
        self.assertEqual(one([adf(binding("write", SQL), env="test"), pbi(SQL)]), [])

    def test_missing_location_is_possible_at_most(self):                         # A21
        no_server = {k: v for k, v in SQL.items() if k != "server"}
        self.assertEqual(one([adf(binding("write", SQL)), pbi(no_server)]), [("produces", "possible")])
        no_db = {k: v for k, v in SQL.items() if k != "database"}
        self.assertEqual(one([adf(binding("write", no_db)), pbi(SQL)]), [("produces", "possible")])

    def test_dynamic_or_opaque_never_exact(self):                                # A21
        for res in ("dynamic", "opaque", "partial"):
            self.assertEqual(one([adf(binding("write", SQL, res)), pbi(SQL)]), [("produces", "possible")], res)
        self.assertEqual(one([adf(binding("write", SQL)), pbi(SQL, resolution="partial")]), [("produces", "possible")])

    def test_delete_and_read_are_never_producers(self):                         # A21
        self.assertEqual(one([adf(binding("delete", SQL)), pbi(SQL)]), [("deletes", "exact_static")])
        self.assertEqual(one([adf(binding("read", SQL)), pbi(SQL)]), [("reads", "exact_static")])

    def test_letter_case(self):                                                  # A30
        self.assertEqual(one([adf(binding("write", dict(SQL, object="SALES"))), pbi(SQL)]), [("produces", "possible")])
        upper = dict(LAKE, path="Sales/Daily")
        self.assertEqual(one([adf(binding("write", upper)), pbi(LAKE)]), [("produces", "possible")])

    def test_files_and_folders(self):                                            # A22
        self.assertEqual(one([adf(binding("write", LAKE)), pbi(LAKE)]), [("produces", "exact_static")])
        file_in_folder = dict(LAKE, path="sales/daily/part-0.parquet")
        self.assertEqual(one([adf(binding("write", LAKE)), pbi(file_in_folder)]), [("produces", "possible")])
        url = dict(system="Azure Data Lake Storage Gen2", storage_account="lake",
                   url="https://lake.dfs.core.windows.net/raw/sales/daily")
        self.assertEqual(one([adf(binding("write", LAKE)), pbi(url)]), [("produces", "exact_static")])
        self.assertEqual(one([adf(binding("write", dict(LAKE, container="curated"))), pbi(LAKE)]), [])
        self.assertEqual(one([adf(binding("write", dict(LAKE, storage_account="other"))), pbi(LAKE)]), [])

    def test_unknown_environment_is_visible_not_blocking(self):
        found = detect([adf(binding("write", SQL), env="unknown"), pbi(SQL)])
        self.assertEqual(found[0]["confidence"], "exact_static")
        self.assertIn("environment not declared on both documents", found[0]["evidence"]["notes"])

    def test_deterministic_ids_and_order(self):
        docs = [adf(binding("write", SQL), binding("read", LAKE)), pbi(SQL), pbi(LAKE, doc="q" * 8)]
        first, second = detect(docs), detect(list(reversed(docs)))
        self.assertEqual(first, second)
        self.assertEqual(len({r["relationship_id"] for r in first}), 2)


if __name__ == "__main__":
    unittest.main()
