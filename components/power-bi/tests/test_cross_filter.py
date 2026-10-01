"""Relationship cross-filter direction has one canonical spelling in the shared model.

`singleDirection` (an older default and the spelling pbi-tools-based extracts ended up with) and `oneDirection` (the portable
reader's spelling, and what the Tabular model calls it) mean the same thing and must be indistinguishable downstream.
`bothDirections` stays distinct and keeps its warning; `automatic` is kept as its own value, is not claimed to be single or
both, and is reported."""
import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from test_column_usage import raw_model
from pbidocgen.agent_writer import build_agent_md
from pbidocgen.model_parser import CROSS_FILTER_AUTOMATIC, CROSS_FILTER_BOTH, CROSS_FILTER_ONE, cross_filter_label, \
    normalize_cross_filtering, parse_model
from pbidocgen.renderer import build_payload
from pbidocgen.word_writer import render_docx


def rel(name, behavior="__omit__", **extra):
    r = {"name": name, "fromTable": "Sales", "fromColumn": "Key", "toTable": "Dim", "toColumn": "ID", "isActive": True}
    if behavior != "__omit__":
        r["crossFilteringBehavior"] = behavior
    r.update(extra)
    return r


class CrossFilterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def model(self, *behaviors):
        raw = raw_model()
        raw["model"]["relationships"] = [rel(f"r{i}", b) for i, b in enumerate(behaviors)]
        path = self.root / "model.bim"
        path.write_text(json.dumps(raw))
        return parse_model(path)

    def warnings(self, model, category):
        return [w for w in model["warnings"] if w["category"] == category]

    # ---- the normaliser ------------------------------------------------------------------------------------------

    def test_equivalent_single_direction_values_share_one_spelling(self):
        for value in ("oneDirection", "singleDirection", "OneDirection", " singledirection ", None, "", 1):
            self.assertEqual(normalize_cross_filtering(value), CROSS_FILTER_ONE, repr(value))

    def test_bidirectional_and_automatic_stay_distinct(self):
        for value in ("bothDirections", "BothDirections", 2):
            self.assertEqual(normalize_cross_filtering(value), CROSS_FILTER_BOTH, repr(value))
        for value in ("automatic", "Automatic", 3):
            self.assertEqual(normalize_cross_filtering(value), CROSS_FILTER_AUTOMATIC, repr(value))
        self.assertNotEqual(CROSS_FILTER_ONE, CROSS_FILTER_BOTH)
        self.assertNotEqual(CROSS_FILTER_ONE, CROSS_FILTER_AUTOMATIC)

    def test_an_unrecognised_value_is_kept_as_written(self):
        self.assertEqual(normalize_cross_filtering("sideways"), "sideways")
        self.assertEqual(normalize_cross_filtering(9), "9")
        self.assertEqual(cross_filter_label("sideways"), "sideways")

    # ---- the shared model ----------------------------------------------------------------------------------------

    def test_the_model_holds_one_value_for_every_single_direction_spelling(self):
        m = self.model("singleDirection", "oneDirection", "__omit__")
        self.assertEqual([r["crossFilteringBehavior"] for r in m["relationships"]], [CROSS_FILTER_ONE] * 3)
        self.assertEqual(self.warnings(m, "Bidirectional filter"), [])
        self.assertEqual(self.warnings(m, "Automatic cross-filter"), [])
        self.assertEqual(self.warnings(m, "Unrecognised cross-filter"), [])

    def test_bidirectional_keeps_its_warning_and_single_does_not_get_one(self):
        m = self.model("singleDirection", "bothDirections", "oneDirection")
        self.assertEqual([r["crossFilteringBehavior"] for r in m["relationships"]],
                         [CROSS_FILTER_ONE, CROSS_FILTER_BOTH, CROSS_FILTER_ONE])
        warnings = self.warnings(m, "Bidirectional filter")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["severity"], "warning")

    def test_automatic_is_reported_explicitly_and_is_not_a_bidirectional_warning(self):
        m = self.model("automatic")
        self.assertEqual(m["relationships"][0]["crossFilteringBehavior"], CROSS_FILTER_AUTOMATIC)
        self.assertEqual(self.warnings(m, "Bidirectional filter"), [])
        info = self.warnings(m, "Automatic cross-filter")
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]["severity"], "info")

    def test_an_unrecognised_value_is_reported_not_guessed(self):
        m = self.model("sideways")
        self.assertEqual(m["relationships"][0]["crossFilteringBehavior"], "sideways")
        self.assertEqual(len(self.warnings(m, "Unrecognised cross-filter")), 1)

    def test_tmdl_default_and_explicit_one_direction_are_the_same(self):
        d = self.root / "Demo.SemanticModel" / "definition"
        d.mkdir(parents=True)
        (d / "model.tmdl").write_text("model Model\n")
        (d / "tables").mkdir()
        for name in ("Sales", "Dim"):
            (d / "tables" / f"{name}.tmdl").write_text(f"table {name}\n    column Key\n        dataType: int64\n")
        body = ""
        for name, prop in (("r_default", None), ("r_one", "oneDirection"), ("r_single", "singleDirection"),
                           ("r_both", "bothDirections"), ("r_auto", "automatic")):
            body += f"relationship {name}\n    fromColumn: Sales.Key\n    toColumn: Dim.Key\n"
            if prop:
                body += f"    crossFilteringBehavior: {prop}\n"
        (d / "relationships.tmdl").write_text(body)
        m = parse_model(d.parent)
        got = {r["name"]: r["crossFilteringBehavior"] for r in m["relationships"]}
        self.assertEqual(got, {"r_default": CROSS_FILTER_ONE, "r_one": CROSS_FILTER_ONE, "r_single": CROSS_FILTER_ONE,
                               "r_both": CROSS_FILTER_BOTH, "r_auto": CROSS_FILTER_AUTOMATIC})

    # ---- downstream: documents -----------------------------------------------------------------------------------

    def payload(self, *behaviors):
        return build_payload(self.model(*behaviors), None, None, "Cross filter")

    def relationship_rows(self, md):
        return [line for line in md.splitlines() if line.startswith("| Sales[Key]")]

    def test_agent_document_shows_equivalent_values_identically(self):
        a = self.relationship_rows(build_agent_md(self.payload("singleDirection")))
        b = self.relationship_rows(build_agent_md(self.payload("oneDirection")))
        c = self.relationship_rows(build_agent_md(self.payload("__omit__")))
        self.assertEqual(len(a), 1)
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertIn("single", a[0])

    def test_agent_document_distinguishes_both_and_automatic(self):
        both = self.relationship_rows(build_agent_md(self.payload("bothDirections")))[0]
        auto = self.relationship_rows(build_agent_md(self.payload("automatic")))[0]
        self.assertIn("both", both)
        self.assertNotIn("single", auto)
        self.assertIn("automatic", auto)

    def test_agent_document_keeps_the_bidirectional_warning_and_filter_reach(self):
        md = build_agent_md(self.payload("bothDirections"))
        self.assertIn("Bidirectional filter", md)
        self.assertNotIn("Bidirectional filter", build_agent_md(self.payload("singleDirection")))
        self.assertNotIn("Bidirectional filter", build_agent_md(self.payload("oneDirection")))
        self.assertNotIn("Bidirectional filter", build_agent_md(self.payload("automatic")))

    def word_text(self, *behaviors):
        out = render_docx(self.payload(*behaviors), self.root / "m.docx")
        with zipfile.ZipFile(out) as z:
            return z.read("word/document.xml").decode("utf-8")

    def test_word_document_flags_only_bidirectional_and_automatic(self):
        single = self.word_text("singleDirection")
        one = self.word_text("oneDirection")
        self.assertNotIn("bidirectional", single)
        self.assertNotIn("bidirectional", one)
        self.assertEqual(single.count(">active<"), one.count(">active<"))
        self.assertIn("bidirectional", self.word_text("bothDirections"))
        auto = self.word_text("automatic")
        self.assertIn(">automatic<", auto)
        self.assertNotIn("bidirectional", auto)

    def test_extractor_spellings_give_identical_payload_relationships(self):
        """The two spellings the two extractors produced give the same relationship record after parsing."""
        pbi_tools = self.model("__omit__")["relationships"]          # absent in a pbi-tools Raw extract
        platform_old = self.model("singleDirection")["relationships"]
        portable = self.model("oneDirection")["relationships"]       # the portable reader's spelling
        self.assertEqual(pbi_tools, platform_old)
        self.assertEqual(pbi_tools, portable)


if __name__ == "__main__":
    unittest.main()
