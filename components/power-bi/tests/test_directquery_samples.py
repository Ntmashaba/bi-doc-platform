"""Real public projects: DirectQuery to a remote model, and a thin report (see pbip-samples/README.md)."""
import unittest
from pathlib import Path

from pbidocgen.model_parser import parse_model
from pbidocgen.report_parser import parse_report
from pbidocgen.renderer import build_payload

SAMPLES = Path(__file__).resolve().parent.parent / 'pbip-samples'


class DirectQueryToAnalysisServices(unittest.TestCase):
    def setUp(self):
        self.model = parse_model(SAMPLES / 'directquery-to-analysis-services' / 'My new report.SemanticModel')
        self.payload = build_payload(self.model, None, None, 'DQ to AS')

    def test_directquery_tables_resolve_to_the_remote_model(self):
        dq = [(t['name'], p) for t in self.model['tables'] for p in t['partitions'] if p['mode'] == 'directQuery']
        self.assertEqual(len(dq), 9)
        for name, part in dq:
            src = part['source']
            self.assertEqual(src['sourceType'], 'Power BI semantic model (XMLA endpoint)', name)
            self.assertEqual(src['database'], '16-Starter-Sales Analysis')
            self.assertNotIn('Direct Lake', src['label'])

    def test_remote_source_lists_its_tables_with_storage_mode(self):
        remote = [s for s in self.payload['summary']['sources'] if s['sourceType'].startswith('Power BI semantic model')]
        self.assertEqual(len(remote), 1)
        self.assertEqual(len(remote[0]['tables']), 9)
        modes = {m for r in self.payload['primarySources']['rows'] if r['sourceType'].startswith('Power BI semantic') for m in r['storageModes']}
        self.assertEqual(modes, {'directQuery'})


class ThinReportSample(unittest.TestCase):
    def test_live_connection_is_named_without_quotes(self):
        report = parse_report(SAMPLES / 'thin-report-live-connection' / 'K201-MonthSlicer.Report')
        live = report['liveConnection']
        self.assertEqual(live['kind'], 'Power BI semantic model (XMLA endpoint)')
        self.assertEqual(live['database'], 'K201 - MonthSlicer')
        payload = build_payload(None, report, None, 'K201')
        self.assertEqual(payload['mode'], 'report-only')
        self.assertEqual(payload['liveSource']['database'], 'K201 - MonthSlicer')


if __name__ == '__main__':
    unittest.main()
