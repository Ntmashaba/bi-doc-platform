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
    # One table of every kind a table can be defined by, each recognisable only from its own metadata.
    calculated = lambda dax: [{'name': 'p', 'source': {'type': 'calculated', 'expression': dax}}]   # noqa: E731
    raw['model']['tables'] += [
        {'name': 'Calendar', 'dataCategory': 'Time', 'partitions': calculated('CALENDAR(DATE(2024, 1, 1), DATE(2024, 12, 31))'),
         'columns': [{'name': 'Date', 'type': 'calculatedTableColumn', 'sourceColumn': '[Date]', 'dataType': 'dateTime'},
                     {'name': 'Year', 'type': 'calculated', 'expression': 'YEAR(Calendar[Date])', 'dataType': 'int64'}]},
        {'name': 'LocalDateTable_1f', 'isHidden': True, 'partitions': calculated('Calendar(Date(2020, 1, 1), Date(2020, 12, 31))'),
         'annotations': [{'name': '__PBI_LocalDateTable', 'value': 'true'}],
         'columns': [{'name': 'Date', 'type': 'calculatedTableColumn', 'sourceColumn': '[Date]', 'dataType': 'dateTime'}]},
        # Named like an automatic date table, but the file does not mark it as one.
        {'name': 'LocalDateTable_lookalike', 'partitions': calculated('{1, 2, 3}'),
         'columns': [{'name': 'Value', 'type': 'calculatedTableColumn', 'sourceColumn': '[Value]', 'dataType': 'int64'}]},
        {'name': 'Time Intelligence', 'columns': [{'name': 'Calculation', 'dataType': 'string', 'sourceColumn': 'Name'}],
         'partitions': [{'name': 'p', 'source': {'type': 'calculationGroup'}}],
         'calculationGroup': {'precedence': 10, 'calculationItems': [
             {'name': 'PY', 'ordinal': 1, 'expression': 'CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR(Calendar[Date]))'},
             {'name': 'YTD', 'ordinal': 0, 'expression': 'CALCULATE(SELECTEDMEASURE(), DATESYTD(Calendar[Date]))',
              'formatStringDefinition': {'expression': '"#,0"'}}]}},
        {'name': 'Budget', 'columns': [{'name': 'Budgeted'}],
         'partitions': [{'name': 'Budget', 'source': {'query': 'SELECT Budgeted FROM dbo.Budget', 'dataSource': 'dw'}}]},
        {'name': 'Lake', 'columns': [{'name': 'Qty'}], 'partitions': [{'name': 'Lake', 'mode': 'directLake', 'source': {
            'type': 'entity', 'entityName': 'lake_sales', 'schemaName': 'dbo', 'expressionSource': 'Stage'}}]}]
    # ...and in the model: a hierarchy level, a measure's dependency and both ends of a relationship.
    raw['model']['tables'][0]['hierarchies'][0]['levels'].append({'name': 'Twice', 'column': 'Double'})
    raw['model']['tables'][0]['measures'].append({'name': 'Doubled', 'expression': 'SUM(Sales[Double])'})
    raw['model']['relationships'].append({'name': 'by year', 'fromTable': 'Sales', 'fromColumn': 'Double',
                                          'toTable': 'Calendar', 'toColumn': 'Year'})
    raw['model']['dataSources'] = [{'name': 'dw', 'connectionString': 'Provider=SQLNCLI11;Data Source=server;Initial Catalog=db;Integrated Security=SSPI'}]
    # A native query whose statement is built when the query runs: its text is not in the file.
    raw['model']['tables'].append({'name': 'Dynamic', 'columns': [{'name': 'Value'}], 'partitions': [{'name': 'Dynamic', 'source': {
        'type': 'm', 'expression': 'let\n    S = Sql.Database("server", "db"),\n    Q = Value.NativeQuery(S, "SELECT * FROM " & Text.From(DateTime.LocalNow()))\nin\n    Q'}}]})
    # ...and one whose statement is written in the file.
    raw['model']['tables'].append({'name': 'Native', 'columns': [{'name': 'Value'}], 'partitions': [{'name': 'Native', 'source': {
        'type': 'm', 'expression': 'let\n    S = Sql.Database("server", "db", [Query="SELECT Value FROM dbo.Facts WHERE Year = 2024"])\nin\n    S'}}]})
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
    # The calculated column Sales[Double] in every place a column can be named: a visual, a visual filter, a page
    # filter; the calculated column Calendar[Year] in a report filter.
    double = lambda **more: dict(table='Sales', field='Double', kind='column', **more)   # noqa: E731
    r['pages'][1]['visuals'][0]['fields'].append(double())
    r['pages'][1]['visuals'][0]['filters'] = [double(level='visual', filterType='Advanced', raw='> 10', target='Table')]
    r['pages'][1]['filters'] = [double(level='page', filterType='Basic', raw='', target='Same / page')]
    r['reportFilters'] = [dict(table='Calendar', field='Year', kind='column', level='report', filterType='Basic', raw='2024', target='All pages')]
    for page in r['pages']:
        page.update(width=1280, height=720, pageType='page')
        for visual in page['visuals']:
            visual.update(x=20, y=20, width=300, height=150)
    # Every kind of page the Report view tells apart: a drillthrough page with its drillthrough field, a hidden
    # tooltip page with its tooltip field, and a page whose type the document does not record.
    measure = dict(table='Sales', field='Total', kind='measure', context='Values')
    r['pages'] += [
        {'id': 'p3', 'name': 'Order details', 'pageType': 'drillthrough', 'width': 1280, 'height': 720,
         'filters': [dict(table='Dim', field='ID', kind='column', level='page', filterType='Basic', raw='',
                          target='Order details', drillthrough=True),
                     dict(table='Sales', field='Amount', kind='column', level='page', filterType='Advanced', raw='> 0',
                          target='Order details', isHidden=True, displayName='Positive amounts')],
         'visuals': [{'id': 'd1', 'type': 'card', 'title': 'Order total', 'fields': [measure], 'filters': [],
                      'x': 40, 'y': 40, 'width': 360, 'height': 180},
                     {'id': 'd2', 'type': 'tableEx', 'title': None, 'fields': [dict(table='Dim', field='ID', kind='column', context='Values')],
                      'filters': [dict(table='Dim', field='ID', kind='column', level='visual', filterType='TopN', raw='10',
                                       target='Order details / tableEx')], 'x': 440, 'y': 40, 'width': 600, 'height': 400},
                     {'id': 'd3', 'type': 'shape', 'title': None, 'fields': [], 'filters': [], 'x': 0, 'y': 680, 'width': 1280, 'height': 40}]},
        {'id': 'p4', 'name': 'Sales tooltip', 'pageType': 'tooltip', 'hidden': True, 'width': 320, 'height': 240,
         'filters': [dict(table='Sales', field='Amount', kind='column', level='page', filterType='Basic', raw='',
                          target='Sales tooltip', drillthrough=True)],
         'visuals': [{'id': 't1', 'type': 'card', 'title': None, 'fields': [measure], 'filters': [],
                      'x': 10, 'y': 10, 'width': 300, 'height': 120, 'hidden': True}]},
        {'id': 'p5', 'name': 'Imported page', 'width': 1280, 'height': 720, 'filters': [],
         'visuals': [{'id': 'u1', 'type': 'slicer', 'title': 'Region slicer', 'fields': [dict(table='Dim', field='ID', kind='column')],
                      'filters': []}]}]
    # as the readers write it: a page's filters include its visuals' own filters, at visual level
    r['pages'][2]['filters'] += r['pages'][2]['visuals'][1]['filters']
    r['bookmarks'] = [dict(r['bookmarks'][0], page='p2'), {'name': 'Tooltip state', 'fields': [], 'page': 'p4', 'group': 'Saved views'},
                      {'name': 'Gone', 'fields': [], 'page': 'ReportSectionGone'}, {'name': 'Unplaced', 'fields': []}]
    payload = build_payload(m, r, link(m, r), 'Browser regression fixture')
    html = render_html(payload, folder / 'pbidocgen-browser.html')
    (folder / 'pbidocgen-browser.json').write_text(json.dumps(payload))
    (folder / 'pbidocgen-browser-host.html').write_text(HOST, encoding='utf-8')
    return html


if __name__ == '__main__':
    print(build(sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()))
