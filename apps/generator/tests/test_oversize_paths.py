"""Documents above the publication limit through the CLI exit codes and the portable hub export."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "engines" / "tests"))
from fixtures import adf_factory, pbi_model  # noqa: E402

from bidoc_contracts import Limits, validate_artifact  # noqa: E402
from bidoc_engines import generate as gen  # noqa: E402
from bidoc_engines.envelope import GENERATION_LIMITS  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_generator.cli import main  # noqa: E402
from bidoc_generator.export import export_library  # noqa: E402

MIB = 1024 * 1024


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class Paths(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_cli_local_exits_0_shared_exits_4_and_both_keep_the_file(self):
        factory = adf_factory(self.tmp / "factory")
        base = ["generate", "--engine", "adf", "--source", str(factory), "--kind", "adf_git"]
        with mock.patch.object(gen, "PUBLICATION_LIMITS", replace(Limits(), html_bytes=1000)):
            code, out, err = run(base + ["--output-dir", str(self.tmp / "l")])
            self.assertEqual(code, 0, err)
            self.assertIn("Wrote", out)
            self.assertIn("ARTIFACT_TOO_LARGE", err)
            code, out, err = run(base + ["--output-dir", str(self.tmp / "s"), "--profile", "shared"])
            self.assertEqual(code, 4)
            self.assertIn("Wrote", out)                                  # generated and kept
            self.assertIn("cannot be published", err)
            self.assertNotIn("error: Contract", err)
        self.assertEqual(len(list((self.tmp / "s").glob("*.html"))), 1)

    def test_hub_export_includes_a_document_above_25_mib(self):
        model = pbi_model(self.tmp / "m")
        data = json.loads(model.read_text(encoding="utf-8"))
        data["model"]["tables"][0]["partitions"][0]["source"]["expression"] += "\n// " + "x" * (13 * MIB)
        model.write_text(json.dumps(data), encoding="utf-8")
        res = generate(GenerateRequest(engine="power_bi", source_path=str(model), source_kind="bim",
                                       output_dir=str(self.tmp / "out")))
        self.assertEqual(res.status, "local_only", res.errors)
        self.assertGreater(Path(res.artifact_path).stat().st_size, 25 * MIB)
        meta = export_library([self.tmp / "out"], self.tmp / "hub")
        self.assertEqual([d["document_id"] for d in meta["documents"]], [res.document_id])
        self.assertFalse(any("skipped" in w for w in meta.get("warnings", [])))
        exported = (self.tmp / "hub" / meta["documents"][0]["file"]).read_bytes()
        self.assertGreater(len(exported), 25 * MIB)
        validate_artifact(exported, limits=GENERATION_LIMITS)


if __name__ == "__main__":
    unittest.main()
