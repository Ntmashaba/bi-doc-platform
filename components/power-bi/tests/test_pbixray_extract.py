"""The pbixray extractor: a whole PBIX through the pipeline without pbi-tools (skipped without pbixray)."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    import pbixray  # noqa: F401
except ImportError:  # optional dependency
    pbixray = None

from pbidocgen.pbix_batch import load_extracted, run_batch
from pbidocgen.pbixray_extract import extract_pbix, read_layout

SAMPLES = Path(__file__).resolve().parent.parent / 'pbix-samples'


@unittest.skipIf(pbixray is None, 'pbixray not installed')
class PbixrayExtractorTests(unittest.TestCase):
    def extract(self, name):
        source = SAMPLES / name
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name) / 'x'
        extract_pbix(source, folder, None, 0, Path(self.temp.name) / 'log.txt')
        with zipfile.ZipFile(source) as archive:
            has_model = 'DataModel' in archive.namelist()
        return load_extracted(folder, source, has_model), source

    def test_composite_model_modes_and_sources(self):
        (model, report), source = self.extract('DP500 08 Composite model.pbix')
        modes = [p['mode'] for t in model['tables'] for p in t['partitions']]
        self.assertEqual(modes.count('directQuery'), 4)
        self.assertNotIn('dual', modes)
        # five model tables plus Power BI's private auto date template, which is hidden
        self.assertEqual(len(model['tables']), 6)
        self.assertEqual([t['name'] for t in model['tables'] if t['isHidden']],
                         ['DateTableTemplate_dff96084-cd3e-40b8-86fd-3a856e997fe5'])
        layout = read_layout(source)
        self.assertEqual(len(report['pages']), len(layout['sections']))
        dq = [p['source'] for t in model['tables'] for p in t['partitions'] if p['mode'] == 'directQuery']
        self.assertEqual({(s['sourceType'], s['server'], s['database']) for s in dq},
                         {('SQL Server', 'localhost', 'AdventureWorksDW2022-DP500')})

    def test_dual_storage_mode_is_kept(self):
        (model, _), _ = self.extract('DP500 11 Dual storage mode.pbix')
        modes = [p['mode'] for t in model['tables'] for p in t['partitions']]
        self.assertEqual(modes.count('dual'), 3)

    def test_batch_run_with_extractor(self):
        with tempfile.TemporaryDirectory() as out:
            summary = run_batch(SAMPLES / 'DP500 04 DirectQuery SQL Server.pbix', out, extractor=extract_pbix)
        self.assertEqual((summary['generated'], summary['failed']), (1, 0))


    def test_command_line_matches_pbi_tools_shape(self):
        from pbidocgen.pbixray_extract import main
        with tempfile.TemporaryDirectory() as out:
            target = Path(out) / 'x'
            code = main(['extract', str(SAMPLES / 'DP500 08 Composite model.pbix'), '-extractFolder', str(target),
                         '-modelSerialization', 'Raw'])
            self.assertEqual(code, 0)
            self.assertTrue((target / 'Model' / 'model.bim').is_file())
            self.assertTrue((target / 'Report' / 'report.json').is_file())
        self.assertEqual(main(['nonsense']), 2)


if __name__ == '__main__':
    unittest.main()
