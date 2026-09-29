"""Counts read "1 page", not "1 pages": the plural helper and the places that must use it."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[1] / 'pbidocgen'
TEMPLATE = (FOLDER / 'template.html').read_text(encoding='utf-8')
EXPLORER = (FOLDER / 'explorer.js').read_text(encoding='utf-8')


class PluralLabels(unittest.TestCase):
    def test_the_helper_pluralises_correctly(self):
        helper = next(line for line in TEMPLATE.splitlines() if line.lstrip().startswith('const plural'))
        script = ('const num = n => (n ?? 0).toLocaleString();\n' + helper +
                  '\nconsole.log(JSON.stringify([plural(0,"page"), plural(1,"page"), plural(2,"page"), '
                  'plural(1234,"table"), plural(1,"category","categories"), plural(2,"category","categories")]));')
        out = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), ['0 pages', '1 page', '2 pages', '1,234 tables', '1 category', '2 categories'])

    def test_the_overview_and_lists_use_the_helper(self):
        for expected in ('plural(R.pages.length,"page")', 'plural(M.tables.length,"table")',
                         'plural(M.measures.length,"measure")', 'plural(R.pages.reduce((a,p)=>a+p.visuals.length,0),"visual")'):
            self.assertIn(expected, TEMPLATE)
        for expected in ('plural(rows.length,"measure")', 'plural(visibleSourceGroups.length,"source")'):
            self.assertIn(expected, EXPLORER)

    def test_no_count_is_glued_to_a_hard_coded_plural_noun(self):
        for text, name in ((TEMPLATE, 'template.html'), (EXPLORER, 'explorer.js')):
            for old in ('} pages ·', '} tables ·', '} visuals</div>', '.length} measures', '.length} sources'):
                self.assertNotIn(old, text, f'{name}: use plural() for {old!r}')


if __name__ == '__main__':
    unittest.main()
