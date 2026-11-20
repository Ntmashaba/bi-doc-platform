"""Search index, detected and manual relationships through the API (A19-A24, A28, A33)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_library.api import create_app  # noqa: E402
from bidoc_library.config import Settings  # noqa: E402

SECRET = "s"
MUTATE = {"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc"}
REVENUE_TAG = "2b2c3d4e-0000-4000-8000-000000000002"


def pbi_model(folder: Path, with_revenue=True) -> Path:
    """A Power BI model reading sql1.corp.local / DW / dbo.Sales, the table the ADF fixture writes."""
    measures = [{"name": "Revenue", "expression": "SUM(Sales[Amount])", "lineageTag": REVENUE_TAG}] if with_revenue else []
    model = {"model": {"name": "Sales model", "tables": [{
        "name": "Sales", "lineageTag": "2b2c3d4e-0000-4000-8000-000000000001", "columns": [{"name": "Amount"}],
        "measures": measures,
        "partitions": [{"name": "Sales", "source": {"type": "m", "expression":
                        'let Source = Sql.Database("sql1.corp.local", "DW"),\n'
                        '    T = Source{[Schema="dbo",Item="Sales"]}[Data]\nin\n    T'}}]}]}}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.bim").write_text(json.dumps(model), encoding="utf-8")
    return folder / "model.bim"


class DerivedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.app = create_app(Settings(local_data_dir=self.tmp / "data"), store=self.make_store(), session_secret=SECRET)
        self.c = TestClient(self.app, base_url="http://127.0.0.1:8765")
        self.n = 0
        self.factory = adf_factory(self.tmp / "factory")
        self.model = pbi_model(self.tmp / "pbi")

    def make_store(self):
        return None

    def artifact(self, engine, source, kind, **kw):
        r = generate(GenerateRequest(engine=engine, source_path=str(source), source_kind=kind,
                                     output_dir=str(self.tmp / "out"), environment="Production", **kw))
        self.assertEqual(r.status, "completed", r.errors)
        return Path(r.artifact_path).read_bytes()

    def publish(self, data, etag=None):
        self.n += 1
        headers = {**MUTATE, "Idempotency-Key": f"k{self.n}", **({"If-Match": etag} if etag else {})}
        r = self.c.post("/api/v1/imports", files={"file": ("d.html", data, "text/html")}, headers=headers)
        self.assertIn(r.status_code, (200, 201), r.text)
        return r.json()

    def etag(self, doc_id):
        return self.c.get(f"/api/v1/documents/{doc_id}").headers["ETag"]

    def rel(self, doc_id, **params):
        r = self.c.get(f"/api/v1/documents/{doc_id}/relationships", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def publish_both(self):
        adf = self.publish(self.artifact("adf", self.factory, "adf_git"))
        pbi = self.publish(self.artifact("power_bi", self.model, "bim"))
        return adf, pbi


class Search(DerivedTest):
    def test_index_search_and_filters(self):                                  # A19
        adf, pbi = self.publish_both()
        self.assertEqual(pbi["indexing_state"], "ready")
        r = self.c.get("/api/v1/search-index")
        index = r.json()
        self.assertEqual((index["state"], len(index["documents"])), ("ready", 2))
        self.assertEqual(self.c.get("/api/v1/search-index", headers={"If-None-Match": r.headers["ETag"]}).status_code,
                         304)
        hits = self.c.get("/api/v1/search", params={"q": "copy daily"}).json()["items"]
        self.assertEqual(hits[0]["document_id"], adf["document_id"])
        self.assertIn("Copy daily", hits[0]["section_title"])
        hits = self.c.get("/api/v1/search", params={"q": "revenue"}).json()["items"]
        self.assertEqual([h["document_id"] for h in hits], [pbi["document_id"]])
        self.assertEqual(self.c.get("/api/v1/search", params={"q": "sales", "document_type": "adf"}).json()["items"]
                         [0]["document_type"], "adf")
        self.assertEqual(len(self.c.get("/api/v1/search").json()["items"]), 2)
        self.assertEqual(self.c.get("/api/v1/search", params={"q": "nothing-matches-this"}).json()["items"], [])
        withheld = self.c.get("/api/v1/search", params={"q": "Sql.Database"}).json()["items"]
        self.assertEqual(withheld, [])                                        # withheld code is not searchable

    def test_archived_documents_leave_the_index(self):
        adf, _ = self.publish_both()
        self.c.post(f"/api/v1/documents/{adf['document_id']}/archive",
                    headers={**MUTATE, "If-Match": self.etag(adf["document_id"])})
        docs = self.c.get("/api/v1/search-index").json()["documents"]
        self.assertEqual([d["document_type"] for d in docs], ["power_bi"])


class ServerSearch(DerivedTest):
    """A41: when the index is too large for browsers, /search serves the same semantics."""

    def setUp(self):
        super().setUp()
        self.app = create_app(Settings(local_data_dir=self.tmp / "data2", client_search_index_bytes=0),
                              store=self.make_store(), session_secret=SECRET)
        self.c = TestClient(self.app, base_url="http://127.0.0.1:8765")

    def test_server_mode_matches_browser_semantics(self):
        from bidoc_library.search import search
        self.publish_both()
        self.assertEqual(self.c.get("/api/v1/capabilities").json()["search_mode"], "server")
        full = self.c.get("/api/v1/search-index").json()
        lite = self.c.get("/api/v1/search-index", params={"sections": "false"})
        self.assertTrue(all(d["sections"] == [] for d in lite.json()["documents"]))
        self.assertNotEqual(lite.headers["ETag"], self.c.get("/api/v1/search-index").headers["ETag"])
        for q, f in (("copy daily", {}), ("sales", {}), ("revenue", {"document_type": "power_bi"}), ("", {}),
                     ("a", {}), ("zzz", {})):
            r = self.c.get("/api/v1/search", params={"q": q, "limit": 1, **f}).json()
            expected = search(full["documents"], q, **f)
            self.assertEqual((r["total"], r["items"]), (len(expected), expected[:1]), q)

    def test_small_library_searches_in_the_browser(self):
        app = create_app(Settings(local_data_dir=self.tmp / "data3"), store=self.make_store(), session_secret=SECRET)
        c = TestClient(app, base_url="http://127.0.0.1:8765")
        self.assertEqual(c.get("/api/v1/capabilities").json()["search_mode"], "client")


class Detected(DerivedTest):
    def test_bidirectional_exact_link(self):                                  # A20
        adf, pbi = self.publish_both()
        incoming = self.rel(pbi["document_id"])["incoming"]
        produces = [r for r in incoming if r["kind"] == "produces"]
        self.assertEqual(len(produces), 1)
        link = produces[0]
        self.assertEqual((link["confidence"], link["origin"]), ("exact_static", "detected"))
        self.assertEqual(link["counterpart"]["object_id"], "adf:activity:PL_Load/Copy daily")
        self.assertEqual(link["counterpart"]["parent_object_id"], "adf:pipeline:PL_Load")
        self.assertEqual(link["counterpart"]["document_id"], adf["document_id"])
        outgoing = self.rel(adf["document_id"], object_id="adf:pipeline:PL_Load")["outgoing"]   # pipeline selection
        self.assertEqual([r["counterpart"]["document_id"] for r in outgoing], [pbi["document_id"]])
        objects = self.c.get(f"/api/v1/documents/{pbi['document_id']}/objects").json()["items"]
        source = next(o for o in objects if o["object_id"] == link["object_id"])
        self.assertEqual(source["view"]["view_id"], "pbi.source")

    def test_new_revision_rebuilds_and_history_stays_pinned(self):            # A23, A33
        adf, pbi = self.publish_both()
        old = self.rel(pbi["document_id"])
        self.assertEqual(old["generation"]["state"], "ready")
        second = self.publish(self.artifact("adf", self.factory, "adf_git"), etag=self.etag(adf["document_id"]))
        new = self.rel(pbi["document_id"])
        self.assertNotEqual(new["generation"]["generation_id"], old["generation"]["generation_id"])
        self.assertEqual(new["incoming"][0]["counterpart"]["revision_id"], second["revision_id"])
        pinned = self.rel(pbi["document_id"], generation_id=old["generation"]["generation_id"])
        self.assertEqual(pinned["generation"]["state"], "pinned")
        self.assertEqual(pinned["incoming"][0]["counterpart"]["revision_id"], adf["revision_id"])
        by_rev = self.rel(adf["document_id"], revision_id=adf["revision_id"])     # old ADF revision
        self.assertEqual(by_rev["generation"]["generation_id"], old["generation"]["generation_id"])

    def test_unknown_generation_is_evidence_unavailable(self):
        _, pbi = self.publish_both()
        r = self.c.get(f"/api/v1/documents/{pbi['document_id']}/relationships",
                       params={"generation_id": "00000000-0000-4000-8000-000000000000"})
        self.assertEqual(r.json()["error"]["code"], "EVIDENCE_UNAVAILABLE")

    def test_archived_counterparts_are_not_leaked(self):                      # A28
        adf, pbi = self.publish_both()
        self.c.post(f"/api/v1/documents/{adf['document_id']}/archive",
                    headers={**MUTATE, "If-Match": self.etag(adf["document_id"])})
        derived = self.app.state.store and self.app.routes  # noqa: F841 - keep app alive
        from bidoc_library.derived import Derived
        view = Derived(self.app.state.store).relationships(pbi["document_id"], can_see_archived=False)
        self.assertEqual(view["incoming"], [])

    def test_stale_rebuild_cannot_overwrite_newer(self):
        from bidoc_library.derived import Derived
        adf, pbi = self.publish_both()
        d = Derived(self.app.state.store)
        real = d._build_relationships
        etag = self.etag(adf["document_id"])

        def racing(current, manual, seq):
            g = real(current, manual, seq)
            self.app.state.store.archive(adf["document_id"], etag.strip('"'), "someone")   # a newer change lands
            return g
        d._build_relationships = racing
        store = self.app.state.store
        store.mark_derived_failed(store.read_sequence())                  # force a rebuild
        self.assertEqual(d.refresh()["state"], "stale")
        d._build_relationships = real
        self.assertEqual(d.refresh()["state"], "ready")
        self.assertEqual(d.relationships(pbi["document_id"])["incoming"], [])      # rebuilt at the newer sequence


TMSL_REFRESH_JOB = """\
-- Synthetic: one SQL Agent job that refreshes the seeded model with TMSL. Names no real system.
EXEC msdb.dbo.sp_add_job @job_name = N'Seeded Model Refresh', @enabled = 1, @owner_login_name = N'etl'
EXEC msdb.dbo.sp_add_jobstep @job_name = N'Seeded Model Refresh', @step_id = 1, @step_name = N'Refresh',
    @subsystem = N'ANALYSISCOMMAND', @server = N'SEEDED-SSAS-01\\TABULAR',
    @command = N'{"refresh": {"type": "full", "objects": [{"database": "SeededModel"}]}}'
EXEC msdb.dbo.sp_add_jobserver @job_name = N'Seeded Model Refresh'
"""


class SqlAgentLineage(DerivedTest):
    """rel-rules/2: a SQL Agent job that refreshes the Analysis Services model a Power BI model reads."""

    def pbi_reading_the_model(self, database="SeededModel"):
        model = {"model": {"name": "Seeded report model", "tables": [{
            "name": "Sales", "lineageTag": "3c3d4e5f-0000-4000-8000-000000000001", "columns": [{"name": "Amount"}],
            "partitions": [{"name": "Sales", "source": {"type": "m", "expression":
                            f'let Source = AnalysisServices.Database("SEEDED-SSAS-01\\TABULAR", "{database}")'
                            '\nin\n    Source'}}]}]}}
        folder = self.tmp / f"pbi-{database}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.bim").write_text(json.dumps(model), encoding="utf-8")
        return self.publish(self.artifact("power_bi", folder / "model.bim", "bim"))

    def publish_job(self):
        script = self.tmp / "jobs" / "refresh.sql"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(TMSL_REFRESH_JOB, encoding="utf-8")
        return self.publish(self.artifact("sql_agent", script, "agent_script"))

    def thin_report(self, database="SeededModel"):
        """A live-connected PBIR report: it reads the model through its connection, statically resolved."""
        report = self.tmp / f"thin-{database}" / "Seeded.Report"
        (report / "definition" / "pages").mkdir(parents=True, exist_ok=True)
        (report / "definition.pbir").write_text(json.dumps({"version": "4.0", "datasetReference": {"byConnection": {
            "connectionString": f"Data Source=SEEDED-SSAS-01\\TABULAR;Initial Catalog={database}"}}}), encoding="utf-8")
        (report / "definition" / "report.json").write_text("{}", encoding="utf-8")
        (report / "definition" / "pages" / "pages.json").write_text('{"pageOrder": []}', encoding="utf-8")
        return self.publish(self.artifact("power_bi", report, "pbir"))

    def test_job_refreshing_the_model_a_live_report_reads(self):
        job, pbi = self.publish_job(), self.thin_report()
        incoming = self.rel(pbi["document_id"])["incoming"]
        self.assertEqual([(r["kind"], r["confidence"]) for r in incoming], [("produces", "exact_static")])
        link = incoming[0]
        self.assertEqual((link["counterpart"]["document_id"], link["counterpart"]["document_type"]),
                         (job["document_id"], "sql_agent"))
        self.assertTrue(link["counterpart"]["object_id"].startswith("sqlagent:step:"))
        self.assertTrue(link["counterpart"]["parent_object_id"].startswith("sqlagent:job:"))
        self.assertIn("the job's refresh (full) targets this model", link["evidence"]["notes"])
        outgoing = self.rel(job["document_id"])["outgoing"]
        self.assertEqual([r["counterpart"]["document_id"] for r in outgoing], [pbi["document_id"]])
        self.assertEqual(self.rel(pbi["document_id"])["generation"]["rule_version"], "rel-rules/2")
        self.assertEqual(self.c.get("/api/v1/capabilities").json()["relationship_schema_version"], "rel-rules/2")

    def test_an_import_query_against_the_model_is_possible(self):
        # Power BI resolves an AnalysisServices.Database query only partly (no table named), so never exact
        self.publish_job()
        pbi = self.pbi_reading_the_model()
        incoming = self.rel(pbi["document_id"])["incoming"]
        self.assertEqual([(r["kind"], r["confidence"]) for r in incoming], [("produces", "possible")])
        self.assertIn("resolution is partial", incoming[0]["evidence"]["notes"])

    def test_a_different_model_is_not_related(self):
        self.publish_job()
        self.assertEqual(self.rel(self.thin_report("OtherModel")["document_id"])["incoming"], [])
        self.assertEqual(self.rel(self.pbi_reading_the_model("OtherModel")["document_id"])["incoming"], [])


class Manual(DerivedTest):
    def link(self, adf, pbi, **kw):
        body = {"source_document_id": adf["document_id"], "source_revision_id": adf["revision_id"],
                "source_object_id": "adf:pipeline:PL_Load", "target_document_id": pbi["document_id"],
                "target_revision_id": pbi["revision_id"], "target_object_id": f"pbi:measure:{REVENUE_TAG}",
                "expected_catalogue_sequence": self.c.get("/api/v1/search-index").json()["generation"],
                "kind": "related_to", "reason": "Revenue is reconciled against this load.", **kw}
        return self.c.post("/api/v1/relationships/manual", json=body, headers=MUTATE)

    def test_lifecycle_audit_and_pinning(self):                               # A24, A33
        adf, pbi = self.publish_both()
        self.assertEqual(self.link(adf, pbi, expected_catalogue_sequence=0).json()["error"]["code"], "SELECTION_STALE")
        self.assertEqual(self.link(adf, pbi, target_object_id="pbi:measure:nope").json()["error"]["code"],
                         "OBJECT_NOT_FOUND")
        self.assertEqual(self.link(adf, pbi, kind="produces", target_object_id=None).status_code, 400)
        created = self.link(adf, pbi)
        self.assertEqual(created.status_code, 201, created.text)
        rid, etag = created.json()["relationship_id"], created.headers["ETag"]
        first_gen = self.rel(pbi["document_id"])
        m = first_gen["manual"][0]
        self.assertEqual((m["confidence"], m["origin"], m["status"], m["direction"]),
                         ("user_asserted", "manual", "active", "incoming"))
        self.assertEqual(self.rel(adf["document_id"])["manual"][0]["direction"], "outgoing")
        patched = self.c.patch(f"/api/v1/relationships/manual/{rid}", json={"reason": "Updated reason."},
                               headers={**MUTATE, "If-Match": etag})
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(self.c.patch(f"/api/v1/relationships/manual/{rid}", json={"reason": "x"},
                                      headers={**MUTATE, "If-Match": etag}).status_code, 409)
        self.assertEqual(self.rel(pbi["document_id"])["manual"][0]["reason"], "Updated reason.")
        pinned = self.rel(pbi["document_id"], generation_id=first_gen["generation"]["generation_id"])
        self.assertEqual(pinned["manual"][0]["reason"], "Revenue is reconciled against this load.")
        # the target measure disappears from a complete snapshot: needs review, never reassigned
        self.publish(self.artifact("power_bi", pbi_model(self.tmp / "pbi", with_revenue=False), "bim"),
                     etag=self.etag(pbi["document_id"]))
        self.assertEqual(self.rel(pbi["document_id"])["manual"][0]["status"], "needs_review")
        self.assertEqual(self.c.delete(f"/api/v1/relationships/manual/{rid}",
                                       headers={**MUTATE, "If-Match": patched.headers["ETag"]}).status_code, 204)
        self.assertEqual(self.rel(pbi["document_id"])["manual"], [])
        audit = self.c.get(f"/api/v1/relationships/manual/{rid}/audit").json()["items"]
        self.assertEqual([a["action"] for a in audit], ["create", "update", "delete"])



from backends import add_variants  # noqa: E402

add_variants(globals(), (Search, ServerSearch, Detected, SqlAgentLineage, Manual))

if __name__ == "__main__":
    unittest.main()
