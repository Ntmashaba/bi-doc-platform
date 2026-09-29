"""Opt-in diagnostic observations for the review findings; regression assertions are in test_review_fixes.py and check_explorer_ui.cjs.
Run: python tests/adversarial_review.py
Outputs observations, including known defects, without changing project sources.
"""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_column_usage import raw_model, report_fixture, field
from pbidocgen.model_parser import parse_model
from pbidocgen.report_parser import parse_report
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.linker import link

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    def payload(raw, report=None):
        path = root / 'model.bim'
        path.write_text(json.dumps(raw))
        model = parse_model(path)
        report = copy.deepcopy(report if report is not None else report_fixture())
        return build_payload(model, report, link(model, report), 'Adversarial review')
    def observation(label, value):
        print(label + ': ' + json.dumps(value, ensure_ascii=False))
    def target(p, name):
        return [{k: r[k] for k in ('column', 'decision', 'usedInReport', 'pageId', 'reviewNotes')}
                for r in p['columns']['rows'] if r['column'] == name]
    raw = raw_model()
    raw['model']['tables'][0]['measures'][0]['detailRowsDefinition'] = {'expression': 'SELECTCOLUMNS(Sales, "Detail", Sales[Unused])'}
    observation('F01 measure detail rows', target(payload(raw), 'Unused'))
    for token in ('//', '--', '/*'):
        raw = raw_model()
        name = 'Amount' + token + 'marker'
        raw['model']['tables'][0]['columns'].append({'name': name})
        raw['model']['tables'][0]['measures'].append({'name': 'Edge', 'expression': f'SUM(Sales[{name}])'})
        report = report_fixture()
        report['pages'][0]['visuals'][0]['fields'].append(field('Edge', 'measure'))
        observation('F02 comment token '+token, target(payload(raw, report), name))
    raw = raw_model()
    raw['model']['tables'].append({'name': 'Rates//USD', 'columns': [{'name': 'Value'}]})
    raw['model']['tables'][0]['measures'].append({'name': 'Rate', 'expression': "SUM('Rates//USD'[Value])"})
    report = report_fixture()
    report['pages'][0]['visuals'][0]['fields'].append(field('Rate', 'measure'))
    observation('F02 quoted table comment token', target(payload(raw, report), 'Value'))
    raw = raw_model()
    raw['model']['tables'].append({'name': 'Param', 'columns': [{'name': 'Choice', 'type': 'calculatedTableColumn', 'sourceColumn': '[Value1]'}],
                                  'partitions': [{'name': 'Param', 'source': {'type': 'calculated', 'expression': '{("Total", NAMEOF(Sales[Total]), 0)}'}}]})
    report = report_fixture()
    for page in report['pages']:
        page['visuals'][0]['fields'] = [field('Choice', table='Param')]
    observation('F03 parameter usage', target(payload(raw, report), 'Amount'))
    definition = root / 'Incomplete.Report' / 'definition'
    page = definition / 'pages' / 'present'
    page.mkdir(parents=True)
    (definition / 'report.json').write_text('{"name":"Incomplete"}')
    (definition / 'pages' / 'pages.json').write_text('{"pageOrder":["present","missing"]}')
    (page / 'page.json').write_text('{"displayName":"Present"}')
    report = parse_report(definition.parent)
    observation('F04 missing declared page warnings', report['warnings'])
    observation('F04 deletion result', target(payload(raw_model(), report), 'Unused'))
    (page / 'page.json').write_text('[1]')
    try:
        malformed = parse_report(definition.parent)
        observation('F09 malformed page warnings', malformed['warnings'])
    except Exception as e:
        observation('F09 malformed page', type(e).__name__ + ': ' + str(e))
    p = payload(raw_model())
    html = render_html(p, root / 'probe.html')
    subprocess.run(['node', str(Path(__file__).with_name('check_adversarial.cjs')), str(html)], check=True)
