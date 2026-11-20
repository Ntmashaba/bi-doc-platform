"""Backup passwords in SQL Agent commands never reach a bidoc document, local or shared. Synthetic only."""
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from bidoc_engines.generate import GenerateRequest, generate

NS = 'xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"'
COMMANDS = (
    f"<Restore {NS}><File>x.abf</File><DatabaseName>SeededModel</DatabaseName><Password>Pw-Marker-1</Password></Restore>",
    '{"backup": {"database": "SeededModel", "file": "x.abf", "password": "Pw-Marker-2"}}',
)


class BackupPasswords(unittest.TestCase):
    def test_no_profile_and_no_extra_output_carries_them(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for i, command in enumerate(COMMANDS):
            script = tmp / f"in{i}" / "job.sql"
            script.parent.mkdir()
            script.write_text("EXEC msdb.dbo.sp_add_job @job_name = N'Seeded Backup'\n"
                              "EXEC msdb.dbo.sp_add_jobstep @job_name = N'Seeded Backup', @step_name = N'step',\n"
                              f"    @subsystem = N'ANALYSISCOMMAND', @command = N'{command}'\n", encoding="utf-8")
            for profile in ("local", "shared"):
                out = tmp / f"out{i}-{profile}"
                r = generate(GenerateRequest(engine="sql_agent", source_path=str(script), source_kind="agent_script",
                                             output_dir=str(out), environment="Production", profile=profile,
                                             also=("json", "word", "csv", "agent")))
                self.assertEqual(r.status, "completed", r.errors)
                files = [p for p in out.rglob("*") if p.is_file()]
                self.assertGreater(len(files), 1)
                for path in files:
                    data = path.read_bytes()
                    if path.suffix == ".docx":
                        data = b"".join(zipfile.ZipFile(path).read(n) for n in zipfile.ZipFile(path).namelist())
                    self.assertNotIn(b"Pw-Marker-", data, f"{profile}: {path.name}")


if __name__ == "__main__":
    unittest.main()


class ScopeWarning(unittest.TestCase):
    def test_load_errors_read_as_read_or_analysed(self):
        from bidoc_engines.sql_agent import scope
        payload = {"jobs": [{"identity": "name:j"}], "coverage": {"loadErrors": 1}}
        _, complete, warnings = scope(payload)
        self.assertFalse(complete)
        self.assertIn("1 file(s) could not be read or analysed.", warnings)
