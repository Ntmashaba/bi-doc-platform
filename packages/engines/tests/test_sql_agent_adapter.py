"""SQL Agent adapter: envelope round-trip, object mapping, bindings, scope and identity (v4 5.6).

The script below is synthetic, written for these tests; it names no real system.
"""
import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bidoc_contracts import scope_key, sql_agent_scope, validate_artifact  # noqa: E402
from bidoc_engines.convert import SUPPORTED_NATIVE_SCHEMAS, rerender  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_engines.sql_agent import VIEW_IDS  # noqa: E402

GUID = "1B2C3D4E-0000-4000-8000-000000000001"
GUID_ID = f"guid:{GUID.lower()}"

SCRIPT = f"""\
-- Synthetic test fixture: one SSAS processing job (scripted GUID) and one
-- T-SQL job (name identity), sharing one schedule.
USE [msdb]
GO
EXEC msdb.dbo.sp_add_job
    @job_name = N'SSAS Nightly Processing',
    @enabled = 1,
    @description = N'Full process of the sales cube, then staging cleanup.',
    @owner_login_name = N'sa',
    @job_id = N'{GUID}'
EXEC msdb.dbo.sp_add_jobstep
    @job_id = N'{GUID}',
    @step_id = 1,
    @step_name = N'Process Sales cube',
    @subsystem = N'ANALYSISCOMMAND',
    @server = N'SSAS-EXAMPLE-01',
    @command = N'<Batch Transaction="true" xmlns="http://schemas.microsoft.com/analysisservices/2003/engine">
  <Process>
    <Type>ProcessFull</Type>
    <Object>
      <DatabaseID>SalesDW</DatabaseID>
      <CubeID>Sales</CubeID>
    </Object>
    <Bindings>
      <Binding><DataSourceID>SalesSource</DataSourceID></Binding>
    </Bindings>
  </Process>
</Batch>',
    @retry_attempts = 3,
    @retry_interval = 15
EXEC msdb.dbo.sp_add_jobstep
    @job_id = N'{GUID}',
    @step_id = 2,
    @step_name = N'Cleanup staging',
    @subsystem = N'TSQL',
    @database_name = N'StagingDW',
    @command = N'INSERT INTO dbo.Archive SELECT * FROM dbo.LoadLog; EXEC dbo.usp_Rebuild;'
GO
EXEC msdb.dbo.sp_add_job @job_name = N'Dimension Only', @enabled = 1
EXEC msdb.dbo.sp_add_jobstep
    @job_name = N'Dimension Only',
    @step_id = 1,
    @step_name = N'Touch dims',
    @subsystem = N'TSQL',
    @database_name = N'StagingDW',
    @command = N'UPDATE dbo.DimWatermark SET touched = 1;'
GO
EXEC msdb.dbo.sp_add_schedule
    @schedule_name = N'Weeknights 22:00',
    @enabled = 1,
    @freq_type = 8,
    @freq_interval = 62,
    @freq_subday_type = 1,
    @active_start_time = 220000,
    @active_start_date = 20260101
EXEC msdb.dbo.sp_attach_schedule @job_name = N'SSAS Nightly Processing', @schedule_name = N'Weeknights 22:00'
EXEC msdb.dbo.sp_attach_schedule @job_name = N'Dimension Only', @schedule_name = N'Weeknights 22:00'
GO
"""


def _walk_bindings(manifest):
    for obj in manifest["objects"]:
        for b in obj["bindings"]:
            yield obj, b


class Adapter(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.script = self.tmp / "agent_jobs.sql"
        self.script.write_text(SCRIPT, encoding="utf-8")
        r = generate(GenerateRequest(engine="sql_agent", source_path=str(self.script),
                                     source_kind="agent_script", output_dir=str(self.tmp / "out")))
        self.assertEqual(r.status, "completed", r.errors)
        self.artifact = Path(r.artifact_path).read_bytes()
        self.manifest = validate_artifact(self.artifact, view_ids=VIEW_IDS)

    def test_publishable_envelope(self):
        m = self.manifest
        self.assertEqual(m["document_type"], "sql_agent")
        self.assertEqual(m["source"]["kind"], "agent_script")
        self.assertEqual(m["identity_version"], "sqlagent-identity/1")
        self.assertEqual(m["projection"]["native_schema"], "sqlagent-doc-gen/1")
        self.assertEqual(m["native_payload"]["schema_version"], 1)
        self.assertEqual(m["native_payload"]["generator"].split()[0], "sqlagent-doc-gen")
        self.assertEqual(m["publication"]["snapshot_state"], "complete")

    def test_scope_is_the_selection_of_jobs(self):
        descriptor = {"kind": "selection", "jobs": sorted([GUID_ID, "name:dimension only"])}
        self.assertEqual(self.manifest["publication"]["scope_descriptor"], descriptor)
        self.assertEqual(self.manifest["publication"]["scope_key"], scope_key("sql_agent", descriptor))
        self.assertEqual(descriptor, sql_agent_scope(descriptor["jobs"]))

    def test_object_mapping(self):
        objects = {o["object_id"]: o for o in self.manifest["objects"]}
        job = objects[f"sqlagent:job:{GUID_ID}"]
        self.assertEqual(job["kind"], "pipeline")
        self.assertIsNone(job["parent_object_id"])
        step = objects[f"sqlagent:step:{GUID_ID}/1"]
        self.assertEqual(step["kind"], "activity")
        self.assertEqual(step["parent_object_id"], job["object_id"])
        schedules = [o for o in objects.values() if o["kind"] == "trigger"]
        self.assertEqual(len(schedules), 1, "the shared schedule is one object for both jobs")
        self.assertIsNone(schedules[0]["parent_object_id"])
        self.assertTrue(schedules[0]["object_id"].startswith("sqlagent:schedule:"))

    def test_view_targets_registered(self):
        views = {t["view_id"] for t in self.manifest["navigation"]["targets"]}
        self.assertEqual(views, {"sqlagent.overview", "sqlagent.job", "sqlagent.step", "sqlagent.schedule"})
        self.assertLessEqual(views, VIEW_IDS)

    def test_ssas_bindings_execute_on_ssas_with_flat_scalar_context(self):
        step = {o["object_id"]: o for o in self.manifest["objects"]}[f"sqlagent:step:{GUID_ID}/1"]
        execute = [b for b in step["bindings"] if b["operation"] == "execute"]
        self.assertEqual(len(execute), 1)
        ep = execute[0]["endpoint"]
        self.assertEqual((ep["system"], ep["server"], ep["database"], ep["object"]),
                         ("ssas", "SSAS-EXAMPLE-01", "SalesDW", "Sales"))
        self.assertEqual(execute[0]["normalized_endpoint"]["server"], "ssas-example-01")
        self.assertEqual(execute[0]["invocation_context"],
                         {"subsystem": "ANALYSISCOMMAND", "process_type": "ProcessFull",
                          "process_object": "SalesDW/Sales", "operation_kind": "process",
                          "command_language": "xmla"})

    def test_process_out_of_line_binding_is_a_sql_read(self):
        step = {o["object_id"]: o for o in self.manifest["objects"]}[f"sqlagent:step:{GUID_ID}/1"]
        reads = [b for b in step["bindings"] if b["operation"] == "read"]
        self.assertEqual(len(reads), 1)
        self.assertEqual((reads[0]["endpoint"]["system"], reads[0]["endpoint"]["object"]),
                         ("sql", "SalesSource"))

    def test_invocation_context_is_flat_scalars_only(self):
        for obj, b in _walk_bindings(self.manifest):
            for key, value in b["invocation_context"].items():
                self.assertIsInstance(key, str)
                self.assertIsInstance(value, (str, int, bool, type(None)),
                                      (obj["object_id"], key, value))

    def test_tsql_bindings_are_read_write_execute_never_delete(self):
        operations = {b["operation"] for _, b in _walk_bindings(self.manifest)}
        self.assertNotIn("delete", operations)
        self.assertLessEqual(operations, {"read", "write", "execute"})
        step = {o["object_id"]: o for o in self.manifest["objects"]}[f"sqlagent:step:{GUID_ID}/2"]
        by_op = {}
        for b in step["bindings"]:
            by_op.setdefault(b["operation"], set()).add(b["endpoint"]["object"])
        self.assertIn("usp_Rebuild", by_op.get("execute", set()))
        self.assertIn("Archive", by_op.get("write", set()))
        self.assertIn("LoadLog", by_op.get("read", set()))
        for b in step["bindings"]:
            self.assertEqual(b["endpoint"]["database"], "StagingDW")

    def test_write_extras_csv_uses_the_declared_inventory(self):
        from bidoc_engines.sql_agent import extra_owned_names, write_extras
        payload = self.manifest["native_payload"]["data"]
        written, notes = write_extras(payload, self.tmp / "csv-out" / "doc.html", ("csv",))
        self.assertEqual(notes, [])
        self.assertEqual({Path(p).relative_to(self.tmp / "csv-out").as_posix() for p in written},
                         extra_owned_names("doc"), "the files written are exactly the ones the engine declares")
        self.assertEqual(len(written), 6)

    def test_native_schema_registered_for_conversion(self):
        self.assertIn("sqlagent-doc-gen/1", SUPPORTED_NATIVE_SCHEMAS["sql_agent"])

    def test_rerender_with_command_withheld(self):
        # The trusted renderer renders fully from structured fields when the step
        # command text is absent (as a shared document arrives after projection).
        m = copy.deepcopy(self.manifest)
        for job in m["native_payload"]["data"]["jobs"]:
            for step in job["steps"]:
                step.pop("command", None)
        artifact, stored = rerender(m)
        validate_artifact(artifact, view_ids=VIEW_IDS)
        self.assertIn(b"SSAS Nightly Processing", artifact)
        self.assertEqual(stored["objects"], self.manifest["objects"])


class FolderInputs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def folder(self):
        folder = self.tmp / "jobs"
        folder.mkdir()
        (folder / "agent_jobs.sql").write_text(SCRIPT, encoding="utf-8")
        return folder

    def test_mixed_folder_skips_non_job_sql_and_publishes(self):
        folder = self.folder()
        (folder / "notes.sql").write_text("SELECT 1; -- not a job script\n", encoding="utf-8")
        r = generate(GenerateRequest(engine="sql_agent", source_path=str(folder),
                                     source_kind="agent_folder", output_dir=str(self.tmp / "out")))
        self.assertEqual(r.status, "completed", r.errors)
        m = validate_artifact(Path(r.artifact_path).read_bytes())
        self.assertEqual(m["source"]["kind"], "agent_folder")
        self.assertTrue(any("not job scripts" in w for w in m["projection"]["coverage_warnings"]))

    def test_unreadable_input_means_local_only(self):
        folder = self.folder()
        (folder / "broken.sql").mkdir()      # a directory named like a script: reading it fails
        r = generate(GenerateRequest(engine="sql_agent", source_path=str(folder),
                                     source_kind="agent_folder", output_dir=str(self.tmp / "out")))
        self.assertEqual(r.status, "local_only")
        self.assertTrue(r.artifact_path.endswith(".local.html"))

    def test_shared_profile_round_trips_with_command_withheld(self):
        r = generate(GenerateRequest(engine="sql_agent", source_path=str(self.folder()),
                                     source_kind="agent_folder", output_dir=str(self.tmp / "shared"),
                                     profile="shared"))
        self.assertEqual(r.status, "completed", r.errors)
        raw = Path(r.artifact_path).read_bytes()
        m = validate_artifact(raw)
        self.assertEqual(m["projection"]["profile"], "shared")
        self.assertEqual(m["projection"]["options"], {"query_code": "withheld"})
        for leaked in (b"<Batch Transaction", b"INSERT INTO dbo.Archive"):
            self.assertNotIn(leaked, raw)
        # the document still renders from structured fields alone
        for kept in (b"SSAS-EXAMPLE-01", b"SalesDW", b"Process Sales cube"):
            self.assertIn(kept, raw)

    def test_wrong_kind_rejected(self):
        r = generate(GenerateRequest(engine="sql_agent", source_path=str(self.folder()),
                                     source_kind="adf_git", output_dir=str(self.tmp / "out")))
        self.assertEqual(r.status, "failed")
        self.assertEqual(r.errors[0]["code"], "INVALID_INPUT")


class IdentityFallbacks(unittest.TestCase):
    """Steps without @step_id and same-named job-local schedules must still
    produce unique object/section/target ids (review round 3)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def _manifest(self, script: str):
        src = self.tmp / "jobs.sql"
        src.write_text(script, encoding="utf-8")
        r = generate(GenerateRequest(engine="sql_agent", source_path=str(src),
                                     source_kind="agent_script",
                                     output_dir=str(self.tmp / "out")))
        self.assertEqual(r.status, "completed", r.errors)
        # validate_artifact raises CONTRACT_VIOLATION on any duplicate id
        return validate_artifact(Path(r.artifact_path).read_bytes(), view_ids=VIEW_IDS)

    def test_steps_without_step_id_get_positional_object_ids(self):
        m = self._manifest("""\
EXEC msdb.dbo.sp_add_job @job_name = N'No Step Ids', @enabled = 1
EXEC msdb.dbo.sp_add_jobstep @job_name = N'No Step Ids', @step_name = N'One',
    @subsystem = N'TSQL', @database_name = N'DB1', @command = N'SELECT 1'
EXEC msdb.dbo.sp_add_jobstep @job_name = N'No Step Ids', @step_name = N'Two',
    @subsystem = N'TSQL', @database_name = N'DB1', @command = N'SELECT 2'
""")
        ids = [o["object_id"] for o in m["objects"] if o["kind"] == "activity"]
        # the same deterministic positional fallback the execution graph uses
        self.assertEqual(ids, ["sqlagent:step:name:no step ids/pos:1",
                               "sqlagent:step:name:no step ids/pos:2"])
        self.assertEqual(len(set(ids)), 2)
        self.assertNotIn("None", " ".join(ids))

    def test_same_named_job_local_schedules_stay_independent(self):
        m = self._manifest("""\
EXEC msdb.dbo.sp_add_job @job_name = N'Job A', @enabled = 1
EXEC msdb.dbo.sp_add_jobstep @job_name = N'Job A', @step_id = 1, @step_name = N'x',
    @subsystem = N'TSQL', @command = N'SELECT 1'
EXEC msdb.dbo.sp_add_jobschedule @job_name = N'Job A', @name = N'Nightly',
    @freq_type = 4, @freq_interval = 1, @freq_subday_type = 1, @active_start_time = 220000
EXEC msdb.dbo.sp_add_job @job_name = N'Job B', @enabled = 1
EXEC msdb.dbo.sp_add_jobstep @job_name = N'Job B', @step_id = 1, @step_name = N'y',
    @subsystem = N'TSQL', @command = N'SELECT 2'
EXEC msdb.dbo.sp_add_jobschedule @job_name = N'Job B', @name = N'Nightly',
    @freq_type = 4, @freq_interval = 1, @freq_subday_type = 1, @active_start_time = 230000
""")
        triggers = [o for o in m["objects"] if o["kind"] == "trigger"]
        self.assertEqual(len(triggers), 2, "two independent 'Nightly' schedules, one per job")
        self.assertEqual(len({t["object_id"] for t in triggers}), 2)
        by_id = {t["object_id"]: t for t in triggers}
        self.assertTrue(any("name:job a/" in k for k in by_id), list(by_id))
        self.assertTrue(any("name:job b/" in k for k in by_id), list(by_id))


if __name__ == "__main__":
    unittest.main()
