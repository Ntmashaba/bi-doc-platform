"""A31: each copy/invocation binds to its own endpoint; additions do not move existing IDs."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import pbi_model  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402


def _ds_param():
    return {"name": "DS_Table", "properties": {
        "type": "AzureSqlTable", "linkedServiceName": {"referenceName": "LS_Sql", "type": "LinkedServiceReference"},
        "parameters": {"tbl": {"type": "string"}},
        "typeProperties": {"schema": "dbo", "table": {"value": "@dataset().tbl", "type": "Expression"}}}}


def _copy(name, src, dst):
    ref = lambda t: {"referenceName": "DS_Table", "type": "DatasetReference", "parameters": {"tbl": t}}  # noqa: E731
    return {"name": name, "type": "Copy", "inputs": [ref(src)], "outputs": [ref(dst)],
            "typeProperties": {"source": {"type": "AzureSqlSource"}, "sink": {"type": "AzureSqlSink"}}}


class Bindings(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def manifest(self, engine, source, kind):
        r = generate(GenerateRequest(engine=engine, source_path=str(source), source_kind=kind,
                                     output_dir=str(self.tmp / "out"), profile="shared"))
        self.assertEqual(r.status, "completed", r.errors)
        return validate_artifact(Path(r.artifact_path).read_bytes())

    def test_parameterized_dataset_invocations_bind_separately(self):
        f = self.tmp / "factory"
        items = {"linkedService": [{"name": "LS_Sql", "properties": {"type": "AzureSqlDatabase", "typeProperties": {
                     "connectionString": "Server=tcp:sql1.corp.local,1433;Database=DW;"}}}],
                 "dataset": [_ds_param()],
                 "pipeline": [{"name": "PL", "properties": {"activities": [
                     _copy("A to B", "A", "B"), _copy("C to D", "C", "D")]}}]}
        for kind, rows in items.items():
            (f / kind).mkdir(parents=True)
            for it in rows:
                (f / kind / f"{it['name']}.json").write_text(json.dumps(it), encoding="utf-8")
        objs = {o["object_id"]: o for o in self.manifest("adf", f, "adf_git")["objects"]}

        def ops(activity):
            return {(b["operation"], b["endpoint"]["object"]) for b in objs[f"adf:activity:PL/{activity}"]["bindings"]}
        self.assertEqual(ops("A to B"), {("read", "A"), ("write", "B")})
        self.assertEqual(ops("C to D"), {("read", "C"), ("write", "D")})

    def test_additions_keep_existing_object_ids(self):
        path = pbi_model(self.tmp / "m")
        before = {o["object_id"] for o in self.manifest("power_bi", path, "bim")["objects"]}
        model = json.loads(path.read_text(encoding="utf-8"))
        model["model"]["tables"].insert(0, {"name": "Added", "columns": [{"name": "X"}], "partitions": [
            {"name": "Added", "source": {"type": "m", "expression":
                'let S = Sql.Database("other.corp.local", "Ops"){[Schema="dbo",Item="Added"]}[Data] in S'}}]})
        path.write_text(json.dumps(model), encoding="utf-8")
        after = {o["object_id"] for o in self.manifest("power_bi", path, "bim")["objects"]}
        self.assertTrue(before < after, before - after)


if __name__ == "__main__":
    unittest.main()
