"""A live-connected report paired with a model: per report, from its details file or --model, for every entry point.

The report's own <name>.bidoc-details.json may name the model: {"documentation": {...}, "model": {"path": "..."}}. A
relative path is resolved against that file's folder. --model wins. The pairing is checked before anything is written,
never guessed, and recorded in the document as supplied and not verified.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import adf_factory, pbi_model  # noqa: E402
from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate, resolve_pairing  # noqa: E402
from bidoc_engines.power_bi import InputError  # noqa: E402


def report(folder: Path) -> Path:
    """A report with no model of its own (a live-connected report)."""
    definition = folder / "definition"
    (definition / "pages" / "home").mkdir(parents=True)
    (definition / "pages" / "home" / "page.json").write_text(json.dumps({"name": "home", "displayName": "Home"}))
    (definition / "pages" / "pages.json").write_text(json.dumps({"pageOrder": ["home"]}))
    (definition / "report.json").write_text("{}")
    return folder


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.model = pbi_model(self.tmp / "Models" / "Sales")
        self.report = report(self.tmp / "Reports" / "Sales.Report")
        self.side = self.report.with_name("Sales.Report.bidoc-details.json")

    def pair(self, value, side=None):
        (side or self.side).write_text(json.dumps(value), encoding="utf-8")

    def run_report(self, source=None, out="out", **kw):
        return generate(GenerateRequest(engine="power_bi", source_path=str(source or self.report), source_kind="pbir",
                                        output_dir=str(self.tmp / out), **kw))

    def data(self, result):
        self.assertEqual(result.status, "completed", result.errors)
        return validate_artifact(Path(result.artifact_path).read_bytes())["native_payload"]["data"]

    def refused(self, *phrases, **kw):
        result = self.run_report(out="refused", **kw)
        self.assertEqual((result.status, result.errors[0]["code"]), ("failed", "INVALID_INPUT"))
        for phrase in phrases:
            self.assertIn(phrase, result.errors[0]["message"])
        self.assertFalse((self.tmp / "refused").exists() and any((self.tmp / "refused").glob("*.html")))


class Paired(Base):
    def test_the_details_file_pairs_the_report_relative_to_its_own_folder(self):
        self.pair({"documentation": {"folder": "Sales"}, "model": {"path": "../Models/Sales/model.bim"}})
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        cwd = os.getcwd()
        os.chdir(elsewhere)                                  # never resolved against the current directory
        self.addCleanup(os.chdir, cwd)
        for profile in ("local", "shared"):
            with self.subTest(profile=profile):
                data = self.data(self.run_report(out=profile, profile=profile))
                self.assertEqual(data["mode"], "combined")
                self.assertEqual(data["modelPairing"], {"model": "model.bim", "selectedBy": "Sales.Report.bidoc-details.json",
                                                        "verified": False})
                self.assertEqual(data["documentation"]["folder"], "Sales")
                note = next(w["message"] for w in data["report"]["warnings"] if w["category"] == "Model pairing")
                self.assertEqual(note, "This report is documented with the model model.bim, named in "
                                       "Sales.Report.bidoc-details.json. The pairing is not verified against the report's "
                                       "live connection: the model's server identity and how current it is are not "
                                       "checked, and an ABF backup is an offline snapshot. Field resolution is checked "
                                       "against this model only.")
        self.assertNotIn("Models", json.dumps(data["modelPairing"]), "the document names the model, not where it lives")

    def test_an_absolute_path_and_a_model_folder_pair_too(self):
        self.pair({"model": {"path": str(self.model.parent)}})
        data = self.data(self.run_report())
        self.assertEqual(data["modelPairing"]["model"], "Sales")
        self.assertEqual((data.get("documentation") or {}).get("folder", ""), "", "a file that only pairs a model sets no details")

    def test_explicit_model_wins_and_says_so(self):
        self.pair({"model": {"path": "../Models/Sales/model.bim"}})
        other = shutil.copy(self.model, self.tmp / "Other.bim")
        pairing = resolve_pairing("power_bi", self.report, explicit=str(other))
        self.assertEqual((Path(pairing.path).name, pairing.selected_by), ("Other.bim", "--model"))
        self.assertEqual(pairing.notes, ["--model was given, so the model '../Models/Sales/model.bim' named in "
                                         "Sales.Report.bidoc-details.json is not used."])
        data = self.data(self.run_report(model_path=pairing.path, model_selected_by=pairing.selected_by,
                                         model_name=pairing.name))
        self.assertEqual(data["modelPairing"]["selectedBy"], "--model")

    def test_two_reports_can_name_the_same_model(self):
        second = report(self.tmp / "Reports" / "Margin.Report")
        for side in (self.side, second.with_name("Margin.Report.bidoc-details.json")):
            self.pair({"model": {"path": "../Models/Sales/model.bim"}}, side)
        first, other = self.data(self.run_report(out="a")), self.data(self.run_report(second, out="b"))
        self.assertEqual(first["modelPairing"]["model"], other["modelPairing"]["model"])
        self.assertEqual((first["mode"], other["mode"]), ("combined", "combined"))

    def test_without_a_pairing_nothing_changes(self):
        data = self.data(self.run_report())
        self.assertNotIn("modelPairing", data)
        self.assertEqual(resolve_pairing("power_bi", self.report).path, None)
        self.assertEqual(resolve_pairing("adf", adf_factory(self.tmp / "factory")).path, None)


class Refused(Base):
    def test_a_missing_or_unsupported_model(self):
        self.pair({"model": {"path": "../Models/Gone.abf"}})
        self.refused("Sales.Report.bidoc-details.json pairs the model '../Models/Gone.abf', which was not found")
        (self.tmp / "notes.txt").write_text("x")
        self.pair({"model": {"path": "../notes.txt"}})
        self.refused("which is not a supported model")
        (self.tmp / "Empty").mkdir()
        self.pair({"model": {"path": "../Empty"}})
        self.refused("a folder with no model.bim or TMDL definition in it")

    def test_a_badly_formed_pairing(self):
        self.pair({"folder": "Sales", "model": {"path": "x.bim"}})
        self.refused('keeps its documentation fields under "documentation"')
        self.pair({"model": "../Models/Sales/model.bim"})
        self.refused('"model" must be {"path": "<model file or folder>"}')
        self.pair({"model": {"path": "x.bim", "verified": True}})
        self.refused('"model" must be')

    def test_only_a_power_bi_report_is_paired(self):
        factory = adf_factory(self.tmp / "factory")
        self.pair({"model": {"path": str(self.model)}}, self.tmp / "factory.bidoc-details.json")
        result = generate(GenerateRequest(engine="adf", source_path=str(factory), source_kind="adf_git",
                                          output_dir=str(self.tmp / "o")))
        self.assertEqual(result.status, "failed")
        self.assertIn("only a Power BI report is paired with a model", result.errors[0]["message"])
        with self.assertRaisesRegex(InputError, "only a Power BI report"):
            resolve_pairing("adf", factory, explicit=str(self.model))

    def test_a_model_is_never_replaced(self):
        other = pbi_model(self.tmp / "Other")
        self.pair({"model": {"path": str(other)}}, self.model.with_name("model.bim.bidoc-details.json"))
        result = generate(GenerateRequest(engine="power_bi", source_path=str(self.model), source_kind="bim",
                                          output_dir=str(self.tmp / "o")))
        self.assertEqual(result.status, "failed")
        self.assertIn("must not replace", result.errors[0]["message"])

    def test_a_model_that_needs_extracting_is_left_to_the_generator_app(self):
        backup = self.tmp / "Models" / "Sales.abf"
        backup.write_bytes(b"backup")
        self.pair({"model": {"path": "../Models/Sales.abf"}})
        self.assertTrue(resolve_pairing("power_bi", self.report).needs_extraction)
        self.refused("pairs the model Sales.abf, which has to be extracted first")


if __name__ == "__main__":
    unittest.main()
