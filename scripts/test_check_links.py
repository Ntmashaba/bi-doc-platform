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


if __name__ == "__main__":
    unittest.main()
