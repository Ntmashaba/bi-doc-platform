"""Duplicate job definitions through the adapter: two objects, never one merged job. Synthetic scripts only."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from bidoc_contracts import locate_manifest
from bidoc_engines.generate import GenerateRequest, generate

JOB = """EXEC msdb.dbo.sp_add_job @job_name = N'Nightly', @owner_login_name = N'etl'
EXEC msdb.dbo.sp_add_jobstep @job_name = N'Nightly', @step_id = 1, @step_name = N'{step}',
    @subsystem = N'TSQL', @command = N'SELECT 1'
"""


class DuplicateJobs(unittest.TestCase):
    def test_dev_and_prod_are_two_pipelines_each_with_its_own_step(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        folder = tmp / "jobs"
        folder.mkdir()
        (folder / "dev.sql").write_text(JOB.format(step="dev step"), encoding="utf-8")
        (folder / "prod.sql").write_text(JOB.format(step="prod step"), encoding="utf-8")
        r = generate(GenerateRequest(engine="sql_agent", source_path=str(folder), source_kind="agent_folder",
                                     output_dir=str(tmp / "out"), environment="Production"))
        self.assertEqual(r.status, "completed", r.errors)
        m = json.loads(locate_manifest(Path(r.artifact_path).read_bytes()).body)
        jobs = sorted(o["object_id"] for o in m["objects"] if o["kind"] == "pipeline")
        self.assertEqual(jobs, ["sqlagent:job:name:nightly", "sqlagent:job:name:nightly@prod.sql"])
        steps = {o["parent_object_id"]: o["label"] for o in m["objects"] if o["kind"] == "activity"}
        self.assertEqual(steps, {"sqlagent:job:name:nightly": "dev step",
                                 "sqlagent:job:name:nightly@prod.sql": "prod step"})
        codes = [i["code"] for i in m["native_payload"]["data"]["issues"]]
        self.assertIn("job-duplicate", codes)


if __name__ == "__main__":
    unittest.main()
