"""rel-rules/2 (handoff A20, A21, A22; SQL Agent design v4.1 section 8)."""
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


# -- SQL Agent as a source (rel-rules/2) -------------------------------------------------------------------------

AS_SOURCE = dict(system="SQL Server Analysis Services", server="ASSERVER", instance="TABULAR", database="SalesModel")
TSQL = dict(system="sql", database="DW", schema="dbo", object="Sales")       # no server: the job's own server


def ssas(operation_kind="refresh", language="tmsl", process_type="full", **ep):
    target = dict(dict(system="ssas", server="ASSERVER\\TABULAR", database="SalesModel"), **ep)
    b = binding("execute", target, bid=f"execute:{sorted(target.items())}:{operation_kind}")
    b["invocation_context"] = {"subsystem": "ANALYSISCOMMAND", "process_type": process_type,
                               "process_object": "/".join(p for p in (target.get("database"), target.get("object")) if p),
                               "operation_kind": operation_kind, "command_language": language}
    return b


def agent(*bindings, env="production", doc="j" * 8):
    return Document(doc, "r-" + doc, "sql_agent", env, (
        {"object_id": "sqlagent:job:J", "kind": "pipeline", "parent_object_id": None, "bindings": []},
        {"object_id": "sqlagent:step:J/1", "kind": "activity", "parent_object_id": "sqlagent:job:J",
         "bindings": list(bindings)}))


class SqlAgentSource(unittest.TestCase):
    def test_tmsl_refresh_of_the_model_a_report_reads_is_exact(self):
        found = detect([agent(ssas()), pbi(AS_SOURCE)])
        self.assertEqual([(r["kind"], r["confidence"]) for r in found], [("produces", "exact_static")])
        r = found[0]
        self.assertEqual((r["source_object_id"], r["source_parent_object_id"], r["target_object_id"]),
                         ("sqlagent:step:J/1", "sqlagent:job:J", "pbi:source:x"))
        self.assertIn("the job's refresh (full) targets this model", r["evidence"]["notes"])

    def test_live_connection_with_a_composite_server(self):
        live = dict(system="SQL Server Analysis Services", server="asserver\\tabular", database="SalesModel")
        self.assertEqual(one([agent(ssas()), pbi(live)]), [("produces", "exact_static")])

    def test_xmla_names_the_database_by_id_so_possible_at_most(self):
        found = detect([agent(ssas("process", "xmla", "ProcessFull", object="Sales")), pbi(AS_SOURCE)])
        self.assertEqual([(r["kind"], r["confidence"]) for r in found], [("produces", "possible")])
        notes = found[0]["evidence"]["notes"]
        self.assertTrue(any("XMLA DatabaseID" in n for n in notes), notes)
        self.assertIn("the job's process (ProcessFull) targets Sales in this model", notes)

    def test_contradictions_never_join(self):
        for other in (dict(AS_SOURCE, server="OTHER"), dict(AS_SOURCE, instance=None),
                      dict(AS_SOURCE, instance="MULTIDIM"), dict(AS_SOURCE, database="FinanceModel")):
            with self.subTest(other=other):
                self.assertEqual(one([agent(ssas()), pbi(other)]), [])
        self.assertEqual(one([agent(ssas(), env="test"), pbi(AS_SOURCE)]), [])

    def test_local_instance_or_letter_case_is_possible(self):
        self.assertEqual(one([agent(ssas(server=None)), pbi(AS_SOURCE)]), [("produces", "possible")])
        self.assertEqual(one([agent(ssas(database="salesmodel")), pbi(AS_SOURCE)]), [("produces", "possible")])

    def test_only_processing_produces(self):
        for kind in ("backup", "restore", "alter", "delete", "clearcache", "sync"):
            with self.subTest(kind=kind):
                self.assertEqual(one([agent(ssas(kind, "xmla", None)), pbi(AS_SOURCE)]), [])

    def test_a_model_is_never_a_sql_table(self):
        # a cube named like a table: the SSAS target must not match the SQL source, nor the reverse
        cube = ssas("process", "xmla", "ProcessFull", database="DW", object="Sales")
        self.assertEqual(one([agent(cube), pbi(dict(SQL, database="DW", object="Sales"))]), [])
        self.assertEqual(one([agent(binding("write", dict(TSQL, object="SalesModel"))), pbi(AS_SOURCE)]), [])

    def test_tsql_write_without_a_server_is_possible(self):
        found = detect([agent(binding("write", TSQL)), pbi(dict(SQL, database="DW"))])
        self.assertEqual([(r["kind"], r["confidence"]) for r in found], [("produces", "possible")])
        self.assertIn("server known on one side only", found[0]["evidence"]["notes"])
        self.assertEqual(one([agent(binding("read", TSQL)), pbi(dict(SQL, database="DW"))]), [("reads", "possible")])

    def test_executing_a_procedure_is_not_a_relationship(self):
        proc = dict(system="sql", database="DW", schema="dbo", object="usp_Load")
        self.assertEqual(one([agent(binding("execute", proc)), pbi(dict(SQL, database="DW", object="usp_Load"))]), [])

    def test_adf_is_not_compared_with_analysis_services(self):
        self.assertEqual(one([adf(binding("write", dict(system="Azure SQL Database", server="ASSERVER",
                                                        database="SalesModel", object="x"))),
                              pbi(dict(AS_SOURCE, object="x"))]), [])

    def test_mixed_sources_are_deterministic(self):
        docs = [adf(binding("write", SQL)), agent(ssas()), pbi(SQL), pbi(AS_SOURCE, doc="q" * 8)]
        first, second = detect(docs), detect(list(reversed(docs)))
        self.assertEqual(first, second)
        self.assertEqual(sorted(r["source_document_id"] for r in first), ["a" * 8, "j" * 8])


if __name__ == "__main__":
    unittest.main()
