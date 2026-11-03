"""A document generated for the library cannot be edited and saved from the page.

Its details editors ("Documentation details" for Power BI, "Factory details" for Data Factory) save by
rewriting the whole file, which breaks the manifest's integrity hash; the library and `bidoc publish` would
then refuse the copy. Where the manifest is present the details are shown read-only. A document rendered by
an engine on its own has no manifest and keeps its editor.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import adf_factory, pbi_model  # noqa: E402
from bidoc_engines import adf, power_bi  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402

CHECK = Path(__file__).with_name("check_sealed_details.cjs")


@unittest.skipUnless(shutil.which("node"), "Node needed to run the generated script")
class SealedDetails(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def check(self, html: Path, engine: str, expect: str):
        result = subprocess.run(["node", str(CHECK), str(html), engine, expect], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def cases(self):
        return (("power_bi", "bim", pbi_model(self.tmp / "model"), power_bi),
                ("adf", "adf_git", adf_factory(self.tmp / "factory"), adf))

    def test_a_library_copy_is_read_only_in_both_engines(self):
        for engine, kind, source, _ in self.cases():
            for profile in ("local", "shared"):
                with self.subTest(engine=engine, profile=profile):
                    result = generate(GenerateRequest(engine=engine, source_path=str(source), source_kind=kind,
                                                      output_dir=str(self.tmp / f"out-{engine}-{profile}"), profile=profile))
                    self.assertEqual(result.status, "completed", result.errors)
                    self.check(Path(result.artifact_path), engine, "sealed")

    def test_an_engine_copy_without_the_manifest_keeps_its_editor(self):
        for engine, kind, source, module in self.cases():
            with self.subTest(engine=engine):
                html = self.tmp / f"{engine}.html"
                html.write_text(module.render(module.load(source, kind)), encoding="utf-8")
                self.check(html, engine, "editable")


if __name__ == "__main__":
    unittest.main()
