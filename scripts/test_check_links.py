"""scripts/check_links.py: relative links and anchors, offline.

    python -m unittest discover -s scripts -p test_check_links.py
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_links  # noqa: E402


class Slugs(unittest.TestCase):
    def test_github_heading_anchors(self):
        cases = {
            "Other outputs beside the document (`--also`)": "other-outputs-beside-the-document---also",
            "4. What breaks if I change this?": "4-what-breaks-if-i-change-this",
            "Reading the verdicts: caveats that matter": "reading-the-verdicts-caveats-that-matter",
            "Data & sources": "data--sources",
            "Power BI ← Data Factory (`--bridge`)": "power-bi--data-factory---bridge",
            "A [linked](x.md) **bold** heading": "a-linked-bold-heading",
        }
        for heading, anchor in cases.items():
            with self.subTest(heading=heading):
                self.assertEqual(check_links.slug(heading), anchor)


class Links(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        patch = mock.patch.object(check_links, "ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guide.md").write_text(
            "# Guide\n\n## Setup\n\n## Setup\n\n<a id=\"custom\"></a>\n\n```\n## Not a heading\n```\n", encoding="utf-8")

    def problems(self, text):
        page = self.root / "README.md"
        page.write_text(text, encoding="utf-8")
        return check_links.check([page])

    def test_good_links_pass(self):
        self.assertEqual(self.problems(
            "[g](docs/guide.md) [s](docs/guide.md#setup) [s2](docs/guide.md#setup-1) [c](docs/guide.md#custom) "
            "[here](#top)\n\n# Top\n\n[ext](https://example.com/x#y) [mail](mailto:a@b.c) ![img](docs/guide.md)\n"), [])

    def test_missing_files_and_anchors_fail(self):
        problems = self.problems("[a](docs/missing.md)\n[b](docs/guide.md#nope)\n[c](docs/guide.md#not-a-heading)\n"
                                 "[d](#absent)\n[e](../outside.md)\n")
        self.assertEqual([p.split(": ", 1)[1] for p in problems], [
            "docs/missing.md does not exist",
            "docs/guide.md#nope has no heading with the anchor #nope",
            "docs/guide.md#not-a-heading has no heading with the anchor #not-a-heading",
            "#absent has no heading with the anchor #absent",
            "../outside.md points outside the repository",
        ])
        self.assertTrue(problems[0].startswith("README.md:1: "))

    def test_links_in_code_are_ignored(self):
        self.assertEqual(self.problems("`[x](missing.md)`\n\n```\n[y](missing.md)\n```\n"), [])

    def broken(self, text):
        return [p.split(": ", 1)[1] for p in self.problems(text)]


class Fences(Links):
    """A fence closes only with the same character, at least as long; code inside is never read as links."""

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        self.assertEqual(self.broken("````\n```\n[a](gone-a.md)\n```\n[b](gone-b.md)\n````\n[c](gone-c.md)\n"),
                         ["gone-c.md does not exist"])

    def test_a_closing_fence_may_be_longer(self):
        self.assertEqual(self.broken("```\n[a](gone-a.md)\n`````\n[b](gone-b.md)\n"), ["gone-b.md does not exist"])

    def test_backticks_and_tildes_do_not_close_each_other(self):
        self.assertEqual(self.broken("~~~\n```\n[a](gone-a.md)\n```\n~~~\n[b](gone-b.md)\n"
                                     "```\n~~~\n[c](gone-c.md)\n~~~\n```\n"), ["gone-b.md does not exist"])

    def test_indented_fences_info_strings_and_a_fence_left_open(self):
        self.assertEqual(self.broken("   ```python\n[a](gone-a.md)\n   ```\n[b](gone-b.md)\n"
                                     "~~~~ text with ``` inside\n[c](gone-c.md)\n"), ["gone-b.md does not exist"])
        # four spaces is not a fence (it would be an indented code block), so its link is read
        self.assertEqual(self.broken("    ```\n[d](gone-d.md)\n"), ["gone-d.md does not exist"])
        # a backtick fence's info string may not hold a backtick: this line is inline code, not a fence
        self.assertEqual(self.broken("``` `x` ```\n[e](gone-e.md)\n"), ["gone-e.md does not exist"])

    def test_headings_inside_fences_give_no_anchors(self):
        (self.root / "docs" / "f.md").write_text("````\n# Hidden\n````\n# Shown\n", encoding="utf-8")
        self.assertEqual(self.broken("[a](docs/f.md#shown) [b](docs/f.md#hidden)\n"),
                         ["docs/f.md#hidden has no heading with the anchor #hidden"])

    def test_code_spans_match_runs_of_the_same_length(self):
        self.assertEqual(self.broken("``a ` [x](gone-x.md) b`` [y](gone-y.md) ` unmatched [z](gone-z.md)\n"),
                         ["gone-y.md does not exist", "gone-z.md does not exist"])


class Syntax(Links):
    def test_reference_links_and_their_definitions(self):
        text = ("[Guide][g] and [setup][] and [Setup] and ![img][pic]\n\n"
                "[g]: docs/guide.md\n[setup]: <docs/guide.md#setup> \"Setup\"\n  [PIC]: docs/guide.md\n"
                "[bad]: docs/gone.md\n[typo][nope]\n")
        self.assertEqual(self.broken(text), ["docs/gone.md does not exist", "[nope] is a reference with no definition"])

    def test_undefined_references_in_a_file_without_definitions_are_prose(self):
        self.assertEqual(self.broken("Budget alerts are not caps. [3][4]\n- [ ] a task\n"), [])

    def test_escaped_encoded_and_bracketed_destinations(self):
        (self.root / "docs" / "a file (v2).md").write_text("# Top\n", encoding="utf-8")
        good = ("[a](<docs/a file (v2).md>) [b](docs/a%20file%20%28v2%29.md#top) [c](docs/a\\ file\\ \\(v2\\).md) "
                "[d](docs/guide.md \"a title\") [e](docs/guide.md 'single') [f](docs/guide.md (paren))\n")
        self.assertEqual(self.broken(good), [])
        self.assertEqual(self.broken("[x](<docs/a file.md>)\n"), ["docs/a file.md does not exist"])
        # only punctuation can be escaped: `gone\ file.md` is not a destination, so this is not a link (as on GitHub)
        self.assertEqual(self.broken("[y](docs/gone\\ file.md)\n"), [])

    def test_link_text_across_lines_and_nested_brackets(self):
        self.assertEqual(self.broken("[a long\nlink [with] brackets](docs/gone.md)\n"), ["docs/gone.md does not exist"])
        self.assertTrue(self.problems("[a long\nlink](docs/gone.md)\n")[0].startswith("README.md:2: "))

    def test_setext_headings_give_anchors(self):
        (self.root / "docs" / "s.md").write_text("First Part\n==========\n\nSecond part\n---\n", encoding="utf-8")
        self.assertEqual(self.broken("[a](docs/s.md#first-part) [b](docs/s.md#second-part) [c](docs/s.md#third)\n"),
                         ["docs/s.md#third has no heading with the anchor #third"])

    def test_external_links_are_never_fetched(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            self.assertEqual(self.broken("[a](https://example.invalid/x) [b](http://x) [c](mailto:a@b) [d](//cdn.x/y)\n"
                                         "[e]: https://example.invalid/e\n"), [])


if __name__ == "__main__":
    unittest.main()
