"""Generate an explicitly synthetic model/report fixture for browser CI."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_column_usage import raw_model, report_fixture
from pbidocgen.model_parser import parse_model
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.linker import link
raw = raw_model()
for name in ('Sales-US', 'Sales US', "O'Brien", "x');globalThis.reviewMarker=1;//"):
    raw['model']['tables'].append({'name': name, 'columns': [{'name': 'ID'}]})
raw['model']['expressions'] = [{'name': 'Stage', 'kind': 'm', 'expression': 'let\n X = "Café, quoted"\nin X'}]
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / 'model.bim'
    path.write_text(json.dumps(raw))
    m = parse_model(path)
    r = report_fixture()
    for page in r['pages']:
        page.update(width=1280, height=720)
        for visual in page['visuals']:
            visual.update(x=20, y=20, width=300, height=150)
    payload = build_payload(m, r, link(m, r), 'Browser regression fixture')
    render_html(payload, '/tmp/pbidocgen-browser.html')
    Path('/tmp/pbidocgen-browser.json').write_text(json.dumps(payload))
