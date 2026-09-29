"""Extractor that reads a PBIX with pbixray instead of pbi-tools. No shell execution.

For machines without pbi-tools (it needs Windows and Power BI Desktop). The model is rebuilt as
a TMSL model.bim from pbixray's TMSCHEMA tables; the report layout is copied from the PBIX as
Report/report.json. This is an approximation of a pbi-tools extract, not the same output: it has
no shared M queries beyond parameters, no roles/RLS, no hidden-measure flags, no bookmarks
folder. A live-connected PBIX (no DataModel) yields a report-only extract.

    python generate_docs.py --pbix FILE --pbixray        (needs: pip install pbixray)
"""
import json
import zipfile
from pathlib import Path

DATA_TYPE = {2: 'string', 6: 'int64', 8: 'double', 9: 'dateTime', 10: 'decimal', 11: 'boolean', 17: 'binary'}
PARTITION_TYPE = {1: 'query', 2: 'calculated', 4: 'm', 5: 'entity'}
MODE = {0: 'import', 1: 'directQuery', 2: 'import', 3: 'push', 4: 'dual'}
CARDINALITY = {'M': 'many', '1': 'one'}
INTERNAL_PREFIXES = ('H$', 'R$', 'U$')  # VertiPaq storage tables, not model tables
AUTO_DATE_PREFIXES = ('DateTableTemplate_', 'LocalDateTable_')  # Power BI's private auto date/time tables


def _clean(value):
    """None for pandas missing values, plain Python otherwise."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    return value.item() if hasattr(value, 'item') else value


def _text(value):
    value = _clean(value)
    return None if value in (None, '') else str(value)


def _where(frame, column, value):
    """Rows with frame[column] == value; pbixray returns column-less frames when a table is empty."""
    return frame[frame[column] == value] if column in frame.columns else frame.iloc[0:0]


def build_model(path):
    """TMSL dict for the PBIX's embedded model, or None when it has none."""
    try:
        from pbixray import PBIXRay
    except ImportError as exc:
        raise ValueError('The pbixray extractor needs pbixray: pip install pbixray') from exc
    try:
        model = PBIXRay(str(path))
    except Exception as exc:  # pbixray raises its own errors for missing/unsupported models
        if 'DataModel' in str(exc) or 'data model' in str(exc).lower():
            return None
        raise
    columns, partitions = model.tmschema_columns, model.tmschema_partitions
    measures, levels = model.dax_measures, model.tmschema_levels
    hierarchies = model.tmschema_hierarchies
    # tmschema_tables can be incomplete (a measure-only or hidden table may be missing), so gather
    # table names from every place that names one.
    described = {r['Name']: r for _, r in model.tmschema_tables.iterrows()}
    names = list(described)
    for frame in (partitions, columns, hierarchies, measures):
        if 'TableName' in frame.columns:
            names += [n for n in frame['TableName'].tolist() if n not in described and n not in names]
    tables = []
    for name in (n for n in names if not str(n).startswith(INTERNAL_PREFIXES)):
        t = described.get(name, {'Name': name, 'IsHidden': str(name).startswith(AUTO_DATE_PREFIXES),
                                 'Description': None, 'DataCategory': None})
        cols = []
        for _, c in _where(columns, 'TableName', name).iterrows():
            if int(c['Type']) == 3:  # row number
                continue
            col = {'name': c['Name'], 'dataType': DATA_TYPE.get(int(c['DataType']), 'string'),
                   'isHidden': bool(c['IsHidden']), 'sourceColumn': _text(c['SourceColumn']),
                   'formatString': _text(c['FormatString']), 'displayFolder': _text(c['DisplayFolder']),
                   'description': _text(c['Description']), 'dataCategory': _text(c['DataCategory'])}
            if int(c['Type']) == 2:
                col['type'] = 'calculated'
                col['expression'] = _text(c['Expression'])
            cols.append({k: v for k, v in col.items() if v is not None})
        parts = []
        for _, p in _where(partitions, 'TableName', name).iterrows():
            kind = PARTITION_TYPE.get(int(p['Type']))
            if not kind:
                continue
            body = p['QueryDefinition'] or ''
            source = {'type': kind, 'query' if kind == 'query' else 'expression': body}
            parts.append({'name': p['Name'], 'mode': MODE.get(int(p['Mode']), 'import'), 'source': source})
        table_measures = [{k: v for k, v in dict(name=m['Name'], expression=_text(m['Expression']),
                                                  displayFolder=_text(m['DisplayFolder']),
                                                  description=_text(m['Description'])).items() if v is not None}
                          for _, m in _where(measures, 'TableName', name).iterrows()]
        table_hierarchies = []
        for _, h in _where(hierarchies, 'TableName', name).iterrows():
            lv = _where(levels, 'HierarchyID', h['ID']).sort_values('Ordinal')
            table_hierarchies.append({'name': h['Name'], 'levels': [
                {'name': l['Name'], 'column': l['ColumnName']} for _, l in lv.iterrows()]})
        table = {'name': name, 'isHidden': bool(t['IsHidden']), 'columns': cols, 'partitions': parts,
                 'measures': table_measures, 'hierarchies': table_hierarchies,
                 'description': _text(t['Description']), 'dataCategory': _text(t['DataCategory'])}
        tables.append({k: v for k, v in table.items() if v is not None})
    relationships = []
    for _, r in model.relationships.iterrows():
        from_card, _, to_card = str(_clean(r['Cardinality']) or 'M:1').partition(':')
        relationships.append({
            'fromTable': r['FromTableName'], 'fromColumn': r['FromColumnName'],
            'toTable': r['ToTableName'], 'toColumn': r['ToColumnName'], 'isActive': bool(r['IsActive']),
            'crossFilteringBehavior': 'bothDirections' if str(r['CrossFilteringBehavior']).lower().startswith('both')
            else 'singleDirection',
            'fromCardinality': CARDINALITY.get(from_card.strip()[:1], 'many'),
            'toCardinality': CARDINALITY.get(to_card.strip()[:1], 'one')})
    expressions = [{'name': r['ParameterName'], 'kind': 'm', 'expression': r['Expression']}
                   for _, r in model.m_parameters.iterrows()]
    return {'name': Path(path).stem, 'model': {'tables': tables, 'relationships': relationships,
                                                'expressions': expressions}}


def read_layout(path):
    with zipfile.ZipFile(path) as archive:
        try:
            raw = archive.read('Report/Layout')
        except KeyError:
            return None
    for encoding in ('utf-16-le', 'utf-8-sig'):
        try:
            return json.loads(raw.decode(encoding))
        except (ValueError, UnicodeError):
            continue
    return None


def extract_pbix(source, destination, tool, timeout, log):
    """Same signature as pbix_batch.extract_pbix; `tool` and `timeout` are unused."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    notes = ['Extracted with pbixray (approximation of a pbi-tools extract).']
    model = build_model(source)
    if model is not None:
        (destination / 'Model').mkdir(exist_ok=True)
        (destination / 'Model' / 'model.bim').write_text(json.dumps(model), encoding='utf-8')
        notes.append(f"Model: {len(model['model']['tables'])} tables, {len(model['model']['relationships'])} relationships.")
    else:
        notes.append('No embedded model (live connection or report-only).')
    layout = read_layout(source)
    if isinstance(layout, dict):
        (destination / 'Report').mkdir(exist_ok=True)
        (destination / 'Report' / 'report.json').write_text(json.dumps(layout), encoding='utf-8')
        notes.append(f"Report layout: {len(layout.get('sections') or [])} pages.")
    Path(log).write_text('\n'.join(notes) + '\n', encoding='utf-8')


def main(argv=None):
    """pbi-tools-compatible subset, so a caller can run this as its extraction command:

        python -m pbidocgen.pbixray_extract extract FILE.pbix -extractFolder DIR [-modelSerialization Raw]
    """
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 4 or args[0] != 'extract' or '-extractFolder' not in args:
        print('usage: python -m pbidocgen.pbixray_extract extract FILE.pbix -extractFolder DIR', file=sys.stderr)
        return 2
    source = Path(args[1])
    folder = Path(args[args.index('-extractFolder') + 1])
    if not source.is_file():
        print(f'error: not a file: {source}', file=sys.stderr)
        return 2
    try:
        extract_pbix(source, folder, None, 0, folder.parent / 'pbixray-extract.log' if folder.parent.is_dir()
                     else Path('pbixray-extract.log'))
    except Exception as exc:
        print(f'error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(f'Extracted {source.name} to {folder}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
