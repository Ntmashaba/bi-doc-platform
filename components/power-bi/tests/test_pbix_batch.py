import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pbidocgen.pbix_batch import run_batch, output_name, extract_pbix, resolve_tool, load_extracted
from pbidocgen.extracted_report import parse_extracted_report
from pbidocgen.catalog import read_metadata, json_script, METADATA_ID
from test_column_usage import raw_model


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def extracted_fixture(root, with_model=True):
    if with_model:
        write(root / 'Model' / 'database.json', raw_model())
    r = root / 'Report'
    write(r / 'report.json', {'id': 0})
    write(r / 'config.json', {'activeSectionIndex': 0})
    page = r / 'sections' / '000_Overview'
    write(page / 'section.json', {'name': 'stable-page-id', 'displayName': 'Overview', 'ordinal': 0, 'width': 1280, 'height': 720})
    write(page / 'config.json', {'visibility': 1})
    ref = {'Column': {'Expression': {'SourceRef': {'Source': 's'}}, 'Property': 'Amount'}}
    query = {'From': [{'Name': 's', 'Entity': 'Sales'}], 'Select': [ref]}
    visual = page / 'visualContainers' / '000_card'
    write(visual / 'visualContainer.json', {'x': 12, 'y': 30, 'width': 200, 'height': 100})
    write(visual / 'config.json', {'name': 'stable-visual-id', 'singleVisual': {'visualType': 'card', 'prototypeQuery': query}})
    write(visual / 'query.json', {'Commands': [{'SemanticQueryDataShapeCommand': {'Query': query}}]})
    write(page / 'filters.json', [{'expression': {'Column': {'Expression': {'SourceRef': {'Entity': 'Sales'}}, 'Property': 'Amount'}}}])
    write(r / 'bookmarks' / 'Saved' / 'sections' / 'stable-page-id' / 'visualContainers' / 'v.json', query)


class PbixBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.output = self.root / 'html'

    def pbix(self, name, model=True):
        path = self.inputs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('Report/Layout', '{}')
            if model:
                archive.writestr('DataModel', 'synthetic test data')
        return path

    def extractor(self, source, folder, tool, timeout, log):
        log.write_text('Synthetic extractor used by unit test')
        extracted_fixture(folder, with_model='Thin' not in source.name)

    def run(self, result=None):
        # Keep normal unittest lifecycle; batch output isn't a test assertion.
        with contextlib.redirect_stdout(io.StringIO()):
            return super().run(result)

    def batch(self, **kwargs):
        return run_batch(self.inputs, self.output, extractor=self.extractor, **kwargs)

    def test_combined_thin_and_hub_with_automatic_source_locations(self):
        a = self.pbix('Sales.pbix')
        self.pbix('Thin.PBIX', False)
        result = self.batch()
        self.assertEqual((result['generated'], result['failed']), (2, 0))
        self.assertEqual({r['mode'] for r in result['files']}, {'combined', 'report-only'})
        html = (self.output / output_name(a, self.inputs)).read_text()
        self.assertEqual(read_metadata(html)['reportLocation'], str(a))
        self.assertIn('pbi-documentation-hub', (self.output / 'pbi-home.html').read_text())
        self.assertEqual(json.loads((self.output / 'pbix-batch-results.json').read_text())['generated'], 2)

    def test_recursive_duplicate_names_and_uppercase_extension(self):
        a = self.pbix('A/Sales.pbix')
        b = self.pbix('B/Sales.PBIX')
        with self.assertRaisesRegex(ValueError, 'No PBIX'):
            self.batch()
        result = self.batch(recursive=True)
        self.assertEqual(result['generated'], 2)
        self.assertNotEqual(output_name(a, self.inputs).casefold(), output_name(b, self.inputs).casefold())
        before = output_name(a, self.inputs)
        self.pbix('C/Sales.pbix')
        self.assertEqual(before, output_name(a, self.inputs))

    def test_bad_pbix_does_not_block_other_files(self):
        (self.inputs / 'Bad.pbix').write_text('invalid zip')
        self.pbix('Good.pbix')
        result = self.batch()
        self.assertEqual((result['generated'], result['failed']), (1, 1))
        home = (self.output / 'pbi-home.html').read_text()
        self.assertIn('BadZipFile', home)
        self.assertIn('Bad', home)

    def test_saved_accounts_preserved_and_failed_rerun_retains_previous(self):
        source = self.pbix('Sales.pbix')
        self.batch()
        target = self.output / output_name(source, self.inputs)
        text = target.read_text()
        start = text.index('>', text.index('id="' + METADATA_ID + '"')) + 1
        end = text.index('</script>', start)
        metadata = read_metadata(text)
        metadata['connections'] = [{'username': 'CORP\\reader', 'server': 'sql01'}]
        metadata['folder'] = 'Custom/Folder'
        metadata['reportLocation'] = 'https://example.com/reports/Sales'
        target.write_text(text[:start] + json_script(metadata) + text[end:])
        self.batch()
        before = target.read_bytes()
        self.assertEqual(read_metadata(before.decode())['connections'][0]['username'], 'CORP\\reader')
        self.assertEqual(read_metadata(before.decode())['folder'], 'Custom/Folder')
        def fail(*args):
            raise ValueError('Extraction failed')
        result = run_batch(self.inputs, self.output, extractor=fail)
        self.assertEqual(result['failed'], 1)
        self.assertTrue(result['files'][0]['previousHtmlRetained'])
        self.assertEqual(target.read_bytes(), before)
        self.assertIn('Extraction failed', (self.output / 'pbi-home.html').read_text())

    def test_missing_model_is_failure_for_embedded_but_not_thin(self):
        self.pbix('Thin.pbix', True)  # fixture intentionally omits this embedded model
        result = self.batch()
        self.assertEqual(result['failed'], 1)
        self.assertIn('embedded model', result['files'][0]['error'])
        self.assertFalse(list(self.output.glob('Thin*.html')))

    def test_no_report_format_is_not_a_silent_model_only_success(self):
        self.pbix('Sales.pbix')
        def model_only(source, folder, *args):
            write(folder / 'Model' / 'database.json', raw_model())
        result = run_batch(self.inputs, self.output, extractor=model_only)
        self.assertEqual(result['failed'], 1)

    def test_existing_unrelated_html_and_home_are_protected(self):
        source = self.pbix('Sales.pbix')
        self.output.mkdir()
        target = self.output / output_name(source, self.inputs)
        target.write_text('user content')
        result = self.batch()
        self.assertEqual(result['failed'], 1)
        self.assertEqual(target.read_text(), 'user content')
        (self.output / 'pbi-home.html').write_text('user home')
        with self.assertRaisesRegex(ValueError, 'not a generated catalogue'):
            self.batch()

    def test_adapter_keeps_page_visual_geometry_filter_and_bookmark_refs(self):
        extracted_fixture(self.root)
        report = parse_extracted_report(self.root / 'Report', 'Sales')
        page = report['pages'][0]
        self.assertEqual(page['id'], 'stable-page-id')
        self.assertTrue(page['hidden'])
        self.assertEqual(page['width'], 1280)
        visual = page['visuals'][0]
        self.assertEqual((visual['id'], visual['x'], visual['width']), ('stable-visual-id', 12, 200))
        self.assertTrue(any(f['table'] == 'Sales' and f['field'] == 'Amount' for f in visual['fields']))
        self.assertEqual(page['filters'][0]['field'], 'Amount')
        self.assertTrue(report['bookmarks'][0]['fields'])
        self.assertTrue(report['warnings'])  # uncertain coverage must block deletion claims

    def test_extraction_arguments_no_shell_failure_and_timeout(self):
        source = self.pbix('Sales & Q1.pbix')
        log = self.root / 'extract.log'
        with patch('pbidocgen.pbix_batch.subprocess.run') as run:
            run.return_value.returncode = 0
            extract_pbix(source, self.root / 'output with spaces', 'C:/Tools/pbi-tools.exe', 12, log)
            args, kwargs = run.call_args
            self.assertEqual(args[0][2], str(source))
            self.assertEqual(args[0][-2:], ['-modelSerialization', 'Raw'])
            self.assertFalse(kwargs['shell'])
            self.assertEqual(kwargs['timeout'], 12)
            run.return_value.returncode = 3
            with self.assertRaisesRegex(ValueError, 'code 3'):
                extract_pbix(source, self.root / 'out', 'tool', 12, log)
            run.side_effect = subprocess.TimeoutExpired('tool', 12)
            with self.assertRaisesRegex(ValueError, 'timed out'):
                extract_pbix(source, self.root / 'out', 'tool', 12, log)

    def test_cli_rejects_conflicting_options_and_reports_missing_tool(self):
        source = self.pbix('Sales.pbix')
        command = [sys.executable, 'generate_docs.py', '--pbix', str(source)]
        result = subprocess.run(command + ['--output', 'x.html'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--output-dir', result.stderr)
        result = subprocess.run(command + ['--pbi-tools', str(self.root / 'missing.exe')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('pbi-tools Desktop was not found', result.stderr)

    def test_empty_folder_and_invalid_timeout(self):
        with self.assertRaisesRegex(ValueError, 'No PBIX'):
            self.batch()
        self.pbix('Sales.pbix')
        with self.assertRaisesRegex(ValueError, 'greater than zero'):
            self.batch(timeout=0)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX synthetic executable fixture; Windows requires real pbi-tools.exe')
    def test_cli_end_to_end_with_synthetic_extractor_process(self):
        self.pbix('Good.pbix')
        self.pbix('Fail.pbix')
        tool = self.root / 'synthetic-extractor'
        test_dir = str(Path(__file__).resolve().parent)
        repo_dir = str(Path(__file__).resolve().parent.parent)
        tool.write_text(f'#!{sys.executable}\n' + f'import sys\nsys.path[:0] = {repr([test_dir, repo_dir])}\n'
            'from pathlib import Path\nfrom test_pbix_batch import extracted_fixture\n'
            'assert sys.argv[1] == "extract"\n'
            'assert sys.argv[-2:] == ["-modelSerialization", "Raw"]\n'
            'if Path(sys.argv[2]).stem == "Fail":\n print("Synthetic extraction failure"); sys.exit(7)\n'
            'extracted_fixture(Path(sys.argv[sys.argv.index("-extractFolder") + 1]))\n')
        tool.chmod(0o755)
        result = subprocess.run([sys.executable, 'generate_docs.py', '--pbix-folder', str(self.inputs),
            '--output-dir', str(self.output), '--pbi-tools', str(tool)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        summary = json.loads((self.output / 'pbix-batch-results.json').read_text())
        self.assertEqual((summary['generated'], summary['failed']), (1, 1))
        self.assertIn('code 7', summary['files'][0]['error'])
        self.assertTrue((self.output / summary['files'][1]['filename']).exists())
        self.assertIn('Synthetic extraction failure', (self.output / summary['files'][0]['log']).read_text())
