"""Generate an explicitly synthetic model/report fixture for browser CI.

    python tests/build_browser_fixture.py [folder]     (default: the system temp folder)

Writes pbidocgen-browser.html, the same payload as pbidocgen-browser.json, and a page that frames the
document the way the library viewer does (pbidocgen-browser-host.html).
"""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_column_usage import raw_model, report_fixture
from pbidocgen.model_parser import parse_model
from pbidocgen.renderer import build_payload, render_html
from pbidocgen.linker import link

HOST = """<!doctype html><meta charset="utf-8"><title>Viewer host</title>
<iframe id="doc" src="pbidocgen-browser.html" style="width:1400px;height:900px;border:0"></iframe>
<script>
// A stand-in for the library shell: bi-doc-viewer protocol v1 from the parent window.
const replies=[];window.addEventListener('message',e=>{if(e.data&&e.data.protocol==='bi-doc-viewer')replies.push(e.data);});
const send=m=>document.getElementById('doc').contentWindow.postMessage(Object.assign({protocol:'bi-doc-viewer',version:1,channel:'c1',revision_id:'r1'},m),'*');
</script>"""


def build(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    raw = raw_model()
    for name in ('Sales-US', 'Sales US', "O'Brien", "x');globalThis.reviewMarker=1;//"):
        raw['model']['tables'].append({'name': name, 'columns': [{'name': 'ID'}]})
    raw['model']['expressions'] = [
        {'name': 'Stage', 'kind': 'm', 'expression': 'let\n X = "Café, quoted"\nin X', 'queryGroup': 'Staging',
         'lineageTag': '7a000000-0000-4000-8000-000000000001', 'description': 'Text that every table query starts from.'},
        {'name': 'Region', 'kind': 'm', 'queryGroup': 'Staging\\Parameters',
         'expression': '"West" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'},
        {'name': 'fnClean', 'kind': 'm',
         'expression': '(t as table) as table =>\nlet\n    Trimmed = Table.TransformColumns(t, {}),\n'
                       '    #"Kept Rows" = Table.SelectRows(Trimmed, each [Region] = Region)\nin\n    #"Kept Rows"'},
        {'name': 'Cut off', 'kind': 'm', 'expression': 'let\n    Source = Sql.Database("server", "db'}]
    raw['model']['queryGroups'] = [
        {'folder': 'Staging', 'description': 'Queries other queries start from.',
         'annotations': [{'name': 'PBI_QueryGroupOrder', 'value': '0'}]},
        {'folder': 'Staging\\Parameters', 'annotations': [{'name': 'PBI_QueryGroupOrder', 'value': '1'}]}]
    raw['model']['annotations'] = [{'name': 'PBI_QueryOrder', 'value': '["Region","Stage","Sales","fnClean"]'}]
    # The Sales query reads the staging query through the function, and Dim reads the staging query directly.
    raw['model']['tables'][0]['partitions'][0]['source']['expression'] = (
        'let\n    S = Sql.Database("server", "db"),\n    T = S{[Schema="dbo",Item="Orders"]}[Data],\n'
        '    Cleaned = fnClean(T),\n    #"Tagged, with Stage" = Table.AddColumn(Cleaned, "Tag", each Stage)\nin\n    #"Tagged, with Stage"')
    raw['model']['tables'][1]['partitions'] = [{'name': 'Dim', 'source': {'type': 'm', 'expression': 'let Source = Stage in Source'}}]
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
    html = render_html(payload, folder / 'pbidocgen-browser.html')
    (folder / 'pbidocgen-browser.json').write_text(json.dumps(payload))
    (folder / 'pbidocgen-browser-host.html').write_text(HOST, encoding='utf-8')
    return html


if __name__ == '__main__':
    print(build(sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()))
