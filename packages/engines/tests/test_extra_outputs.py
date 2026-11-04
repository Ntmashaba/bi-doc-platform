"""The engines' other outputs from `bidoc`: --also json, word, csv, agent beside the document.

Each is written from the payload the document was rendered from: the full payload for a local document, the
projected one for a shared document. So a shared document's extras withhold exactly what it does (A29 holds across
them), and its JSON is the data its manifest records.
"""
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import ADF_MARKERS, PBI_MARKERS, adf_factory, pbi_model  # noqa: E402
from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines import power_bi  # noqa: E402
from bidoc_engines.generate import EXTRAS, GenerateRequest, generate  # noqa: E402

ALL = ("json", "word", "csv", "agent")


def text_of(path: str) -> str:
    """Everything a file says: a .docx is read part by part, since its XML is compressed."""
    p = Path(path)
    if p.suffix == ".docx":
        with zipfile.ZipFile(p) as z:
            return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())
    return p.read_text(encoding="utf-8-sig")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.model = pbi_model(self.tmp / "model")
        self.factory = adf_factory(self.tmp / "factory")

    def run_engine(self, engine, out="out", **kw):
        source, kind = (self.model, "bim") if engine == "power_bi" else (self.factory, "adf_git")
        result = generate(GenerateRequest(engine=engine, source_path=str(source), source_kind=kind,
                                          output_dir=str(self.tmp / out), **kw))
        self.assertIn(result.status, ("completed", "local_only"), result.errors)
        return result


class Written(Base):
    def test_every_output_beside_the_document_in_both_engines(self):
        self.assertEqual(EXTRAS, ALL)
        for engine in ("power_bi", "adf"):
            for profile in ("local", "shared"):
                with self.subTest(engine=engine, profile=profile):
                    result = self.run_engine(engine, out=f"{engine}-{profile}", profile=profile, also=ALL)
                    html = Path(result.artifact_path)
                    stem = html.with_suffix("")
                    names = sorted(Path(p).relative_to(html.parent).as_posix() for p in result.extra_paths)
                    expected = [f"{stem.name}.agent.md", f"{stem.name}.docx", f"{stem.name}.json"]
                    expected += ([f"{stem.name}.columns.csv"] if engine == "power_bi" else
                                 [f"{stem.name}-csv/{n}.csv" for n in ("edges", "issues", "objects", "usage")])
                    self.assertEqual(names, sorted(expected))
                    manifest = validate_artifact(html.read_bytes())
                    self.assertEqual(json.loads(Path(f"{stem}.json").read_text(encoding="utf-8")),
                                     manifest["native_payload"]["data"], "the JSON is the data the document records")
                    with zipfile.ZipFile(f"{stem}.docx") as z:
                        self.assertIn("word/document.xml", z.namelist())
                    self.assertTrue(Path(f"{stem}.agent.md").read_text(encoding="utf-8").startswith("# "))

    def test_nothing_extra_unless_asked(self):
        result = self.run_engine("adf")
        self.assertEqual(result.extra_paths, [])
        self.assertEqual([p.name for p in Path(result.artifact_path).parent.iterdir() if p.is_file()],
                         [Path(result.artifact_path).name])

    def test_asking_twice_writes_once_and_order_follows_the_request(self):
        result = self.run_engine("adf", also=("agent", "json", "agent"))
        self.assertEqual([Path(p).suffix for p in result.extra_paths], [".md", ".json"])

    def test_an_unknown_output_is_refused_before_anything_is_written(self):
        result = generate(GenerateRequest(engine="adf", source_path=str(self.factory), source_kind="adf_git",
                                          output_dir=str(self.tmp / "refused"), also=("pdf",)))
        self.assertEqual((result.status, result.errors[0]["code"]), ("failed", "INVALID_INPUT"))
        self.assertIn("'pdf'", result.errors[0]["message"])
        self.assertFalse((self.tmp / "refused").exists())

    def test_an_output_that_fails_costs_only_itself(self):
        with mock.patch("adfdocgen.word_writer.render_docx", side_effect=RuntimeError("disk full")):
            result = self.run_engine("adf", also=("json", "word", "agent"))
        self.assertEqual(result.status, "completed")
        self.assertEqual([Path(p).suffix for p in result.extra_paths], [".json", ".md"])
        self.assertIn("The Word document could not be written: RuntimeError: disk full", result.warnings)
        validate_artifact(Path(result.artifact_path).read_bytes())

    def test_column_usage_needs_a_model(self):
        written, notes = power_bi.write_extras({"columns": None}, self.tmp / "report.html", ("csv",))
        self.assertEqual(written, [])
        self.assertEqual(notes, ["No column usage CSV: the document has no semantic model to report columns from."])


class Shared(Base):
    """A29 across the extras: what the shared document withholds, its extras withhold too."""

    def extras_text(self, result):
        return "\n".join(text_of(p) for p in result.extra_paths)

    def test_shared_extras_withhold_what_the_document_does(self):
        for engine, markers in (("power_bi", PBI_MARKERS), ("adf", ADF_MARKERS)):
            with self.subTest(engine=engine):
                shared = self.extras_text(self.run_engine(engine, out=f"s-{engine}", profile="shared", also=ALL))
                for name, marker in markers.items():
                    self.assertNotIn(marker, shared, name)
                self.assertNotIn(str(self.tmp), shared, "no machine path")
                local = self.extras_text(self.run_engine(engine, out=f"l-{engine}", also=ALL))
                self.assertIn(markers["sql_literal"], local, "a local document's extras carry everything it does")

    def test_shared_with_query_code_included_still_cleans_credentials(self):
        text = self.extras_text(self.run_engine("power_bi", profile="shared", query_code="included", also=ALL))
        for name in ("sql_literal", "step_name"):
            self.assertIn(PBI_MARKERS[name], text, name)
        for name in ("odbc_password", "web_token", "entered_row", "url_password", "bearer_token", "api_key"):
            self.assertNotIn(PBI_MARKERS[name], text, name)


if __name__ == "__main__":
    unittest.main()
