"""Portable reader boundaries and coverage."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pbidocgen.portable import model_document, extract
from pbidocgen.live_connection import source_row
from bidoc_engines import power_bi
from bidoc_engines.generate import GenerateRequest, generate


class PortableBoundaries(unittest.TestCase):
    def test_unknown_schema_fails_instead_of_empty_model(self):
        db=sqlite3.connect(':memory:')
        with patch('pbidocgen.portable._metadata',return_value=db):
            with self.assertRaisesRegex(ValueError,'missing Model'):
                model_document('unsupported.abf')

    def test_numeric_live_model_id_is_a_string_endpoint(self):
        row=source_row({'kind':'Published dataset','modelId':123,'server':'','database':''})
        self.assertEqual(row['location'],'123')

    def test_thin_pbix_does_not_invoke_model_reader(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); source=p/'thin.pbix'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('Report/Layout',json.dumps({'sections':[]}).encode('utf-16-le'))
            with patch('pbidocgen.portable.model_document',side_effect=AssertionError('not embedded')):
                extract(source,p/'extract')
            self.assertFalse((p/'extract/Model').exists())
            self.assertEqual(json.loads((p/'extract/extraction.json').read_text())['backend'],'zip')

    def test_partial_shared_output_still_withholds_query_code(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);source=p/'model.bim'
            source.write_text(json.dumps({'_extraction':{'backend':'pbixray','version':'test','inputKind':'abf',
                'complete':False,'warnings':['Unsupported partition'],'limitations':['Incomplete dependency coverage']},
                'model':{'tables':[{'name':'Sales','partitions':[{'name':'Sales','source':{'type':'m',
                    'expression':'let Source = Sql.Database("private-server", "private-db") in Source'}}]}]}}))
            result=generate(GenerateRequest(engine='power_bi',source_path=str(source),source_kind='bim',
                profile='shared',output_dir=str(p/'out')))
            self.assertEqual(result.status,'local_only',result.errors)
            text=Path(result.artifact_path).read_text()
            self.assertNotIn('Sql.Database(',text)
            self.assertIn('[query code withheld]',text)
            self.assertIn('.shared.local.html',result.artifact_path)

    def test_extraction_scope_is_not_complete_when_reader_reports_gaps(self):
        _,complete,warnings=power_bi.scope({'mode':'semantic-only','extraction':{
            'complete':False,'limitations':['Unsupported model feature']}})
        self.assertFalse(complete)
        self.assertIn('Unsupported model feature',warnings)

    def test_a_limitation_that_reached_the_model_warnings_is_not_listed_twice(self):
        line = 'Offline snapshot; no refresh, live server inventory or remote-model retrieval.'
        payload = {'mode': 'semantic-only',
                   'model': {'warnings': [{'message': 'Something else'}, {'message': line}]},
                   'extraction': {'complete': True, 'limitations': [line, 'Only in the extraction']}}
        _, _, warnings = power_bi.scope(payload)
        self.assertEqual(warnings, ['Something else', line, 'Only in the extraction'])   # each once, in order
        self.assertEqual(len(warnings), len(set(warnings)))
