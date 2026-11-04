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
        self.assertIn("The Word document could not be written (RuntimeError). The document and any other outputs were "
                      "written; run again to retry.", result.warnings)
        validate_artifact(Path(result.artifact_path).read_bytes())

    def test_column_usage_needs_a_model(self):
        written, notes = power_bi.write_extras({"columns": None}, self.tmp / "report.html", ("csv",))
        self.assertEqual(written, [])
        self.assertEqual(notes, ["No column usage CSV: the document has no semantic model to report columns from."])


SECRET, LOCAL = "Password=hunter2-SEEDLEAK", "/home/someone/private/SEEDPATH"


class Failures(Base):
    """A failed output says which one and why in general terms; never the exception's text, which can quote secrets."""

    def test_a_failure_message_never_quotes_the_exception(self):
        for exc, expected in ((RuntimeError(f"cannot render {SECRET} at {LOCAL}"), "(RuntimeError)"),
                              (OSError(28, "No space left on device", f"{LOCAL}/x.docx"),
                               "(OSError, No space left on device)"),
                              (ValueError(f"bad value {SECRET}"), "(ValueError)")):
            with self.subTest(exc=type(exc).__name__), mock.patch("adfdocgen.word_writer.render_docx", side_effect=exc):
                result = self.run_engine("adf", out=type(exc).__name__, also=("word",))
                note = [w for w in result.warnings if "Word document" in w]
                self.assertEqual(len(note), 1)
                self.assertIn(expected, note[0])
                joined = json.dumps(result.__dict__)
                self.assertNotIn("SEEDLEAK", joined)
                self.assertNotIn("SEEDPATH", joined)


class Regeneration(Base):
    """Running again into the same folder: the extras beside the document are this run's, or gone."""

    def files(self, result):
        folder = Path(result.artifact_path).parent
        return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())

    def record(self, result):
        return json.loads(Path(result.artifact_path).with_suffix(".outputs.json").read_text(encoding="utf-8"))

    def test_fewer_outputs_remove_the_ones_no_longer_asked_for(self):
        first = self.run_engine("adf", also=ALL)
        stem = Path(first.artifact_path).stem
        self.assertEqual(self.record(first)["outputs"], [f"{stem}.json", f"{stem}.docx"] +
                         [f"{stem}-csv/{n}.csv" for n in ("objects", "usage", "edges", "issues")] + [f"{stem}.agent.md"])
        second = self.run_engine("adf", also=("json",))
        self.assertEqual(self.files(second), sorted([f"{stem}.html", f"{stem}.json", f"{stem}.outputs.json"]))
        self.assertEqual(self.record(second)["outputs"], [f"{stem}.json"])
        third = self.run_engine("adf")
        self.assertEqual(self.files(third), [f"{stem}.html"], "HTML only again: no extras and no record")

    def test_files_the_generator_did_not_write_are_never_touched(self):
        first = self.run_engine("adf")
        folder, stem = Path(first.artifact_path).parent, Path(first.artifact_path).stem
        mine = {f"{stem}.docx": "made by generate_docs.py", "notes.txt": "mine", f"{stem}.outputs.json.bak": "mine",
                "other--12345678.json": "another document's"}
        for name, text in mine.items():
            (folder / name).write_text(text, encoding="utf-8")
        self.run_engine("adf", also=("json",))
        self.run_engine("adf")
        for name, text in mine.items():
            self.assertEqual((folder / name).read_text(encoding="utf-8"), text, name)
        # a record that names files outside this document's own extras is not obeyed
        (folder / f"{stem}.outputs.json").write_text(json.dumps({"schema": "bidoc-outputs/1", "document": f"{stem}.html",
                                                                 "outputs": ["notes.txt", "../outside.txt"]}), encoding="utf-8")
        (self.tmp / "outside.txt").write_text("outside", encoding="utf-8")
        self.run_engine("adf")
        self.assertTrue((folder / "notes.txt").is_file())
        self.assertTrue((self.tmp / "outside.txt").is_file())

    def test_an_output_that_fails_on_regeneration_leaves_no_stale_copy(self):
        first = self.run_engine("adf", also=("word", "json"))
        docx = Path(first.artifact_path).with_suffix(".docx")
        self.assertTrue(docx.is_file())
        with mock.patch("adfdocgen.word_writer.render_docx", side_effect=RuntimeError("boom")):
            second = self.run_engine("adf", also=("word", "json"))
        self.assertFalse(docx.exists(), "an earlier Word document would read as current")
        self.assertTrue(Path(first.artifact_path).with_suffix(".json").is_file())
        self.assertIn("Removed the earlier copy of each output that could not be written this time, so no file beside "
                      "the document is older than it.", second.warnings)

    def test_csv_files_move_together_or_not_at_all(self):
        from adfdocgen import cli as adf_cli
        real = adf_cli.write_csv

        def half(payload, folder):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "objects.csv").write_text("partial", encoding="utf-8")
            raise OSError(5, "Input/output error")
        with mock.patch.object(adf_cli, "write_csv", side_effect=half):
            result = self.run_engine("adf", also=("csv",))
        self.assertEqual(self.files(result), [Path(result.artifact_path).name], "no partial CSV folder, no record")
        self.assertFalse(any(p.name.startswith(".bidoc-extras-") for p in Path(result.artifact_path).parent.iterdir()))
        self.assertIs(adf_cli.write_csv, real)

    def test_the_record_names_the_document_and_revision_and_the_document_is_untouched(self):
        for profile in ("local", "shared"):
            with self.subTest(profile=profile):
                result = self.run_engine("power_bi", out=profile, profile=profile, also=("json", "agent"))
                record = self.record(result)
                self.assertEqual((record["schema"], record["document"], record["document_id"], record["revision_id"],
                                  record["profile"]),
                                 ("bidoc-outputs/1", Path(result.artifact_path).name, result.document_id,
                                  result.revision_id, profile))
                manifest = validate_artifact(Path(result.artifact_path).read_bytes())
                self.assertEqual(manifest["revision_id"], record["revision_id"])

    def test_html_only_generation_is_unchanged(self):
        result = self.run_engine("adf")
        self.assertEqual(self.files(result), [Path(result.artifact_path).name])
        self.assertEqual(result.extra_paths, [])


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
