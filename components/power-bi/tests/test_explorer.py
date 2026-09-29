import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_column_usage import raw_model, report_fixture
from pbidocgen.model_parser import parse_model
from pbidocgen.report_parser import parse_report
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.linker import link
from pbidocgen.agent_writer import build_agent_md


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        path = self.root / 'model.bim'
        path.write_text(json.dumps(raw_model()))
        self.model = parse_model(path)
        self.report = report_fixture()

    def test_graph_preserves_real_edges_and_visual_locations(self):
        p = build_payload(self.model, self.report, link(self.model, self.report), 'Test')
        g = p['columns']['dependencyGraph']
        edges = {(tuple(json.loads(e['dependent'])), tuple(json.loads(e['dependency']))) for e in g['edges']}
        self.assertIn((('m', 'Sales', 'Total'), ('m', 'Sales', 'Base')), edges)
        self.assertIn((('m', 'Sales', 'Base'), ('c', 'Sales', 'Amount')), edges)
        self.assertNotIn((('m', 'Sales', 'Total'), ('c', 'Sales', 'Amount')), edges)
        consumers = [c for c in g['consumers'] if json.loads(c['node'])[-1] == 'Total']
        self.assertEqual({(c['report'], c['pageId'], c['visualId']) for c in consumers},
                         {('Sales', 'p1', 'v1'), ('Sales', 'p2', 'v1')})
        bookmark = next(c for c in g['consumers'] if c['bookmark'])
        self.assertEqual(bookmark['pageId'], '')
        self.assertEqual(bookmark['visualId'], '')
        self.assertEqual(p['schemaVersion'], 2)

    def test_measure_home_table_is_not_filter_feasibility(self):
        p = build_payload(self.model, self.report, link(self.model, self.report), 'Test')
        md = build_agent_md(p)
        self.assertNotIn('a measure homed on table A', md)
        self.assertIn('home table is an organisational location', md)
        self.assertIn('Reachability alone does not establish', md)

    def test_parser_preserves_page_geometry(self):
        page = self.root / 'Layout.Report' / 'definition' / 'pages' / 'p'
        page.mkdir(parents=True)
        (page / 'page.json').write_text('{"displayName":"Layout","width":1280,"height":720}')
        report = parse_report(page.parents[2])
        self.assertEqual((report['pages'][0]['width'], report['pages'][0]['height']), (1280, 720))

    @unittest.skipUnless(shutil.which('node'), 'Node needed for generated JavaScript checks')
    def test_explorer_script_all_modes(self):
        for mode in ('combined', 'semantic', 'report'):
            with self.subTest(mode=mode):
                m = copy.deepcopy(self.model) if mode != 'report' else None
                r = copy.deepcopy(self.report) if mode != 'semantic' else None
                p = build_payload(m, r, link(m, r) if m and r else None, mode)
                html = render_html(p, self.root / (mode + '.html'))
                for check in ('check_explorer_ui.cjs', 'check_diagrams.cjs'):
                    result = subprocess.run(['node', str(Path(__file__).with_name(check)), str(html)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
