"""Offline PBIX / Tabular ABF metadata reader. Never decodes business rows.

PBIXRay decompresses the model. Its public DataFrames omit fields used by
our analysis, so this module alone bridges to the embedded metadata.sqlitedb.
The private data-model bridge is limited to a validated pbixray range (SUPPORTED); SQL is ours, read-only, and
schema checked. Unsupported schemas fail explicitly rather than returning empty
DataFrames. This is a documentation representation, not a round-trip BIM export.
"""
from __future__ import annotations

from contextlib import closing
import importlib.metadata
import json
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from . import connection_strings, input_limits

# Validated: pbixray 0.15.0 to 0.15.5 gave byte-identical documents for 121 public models and pass the canary
# (tests/tools/pbixray_canary.py). Keep in step with the pbixray extras in both pyproject.toml files.
SUPPORTED = '>=0.15.0,<0.16'
SUPPORTED_MIN, SUPPORTED_BELOW = (0, 15, 0), (0, 16)
MODES = {0: 'import', 1: 'directQuery', 2: 'default', 3: 'push', 4: 'dual', 5: 'directLake'}
TYPES = {2: 'string', 6: 'int64', 8: 'double', 9: 'dateTime', 10: 'decimal', 11: 'boolean', 17: 'binary'}
# Microsoft.AnalysisServices.Tabular.ModelPermission (None starts at 1).
PERMISSIONS = {1:'none', 2:'read', 3:'readRefresh', 4:'refresh', 5:'administrator'}


def _release(version):
    """(major, minor, patch) of a plain release, else None. Pre-releases and dev builds are never accepted."""
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', str(version))
    return tuple(int(part) for part in match.groups()) if match else None


def accepts(version):
    """True when `version` is inside SUPPORTED."""
    release = _release(version)
    return release is not None and SUPPORTED_MIN <= release < SUPPORTED_BELOW


def status():
    """Whether the installed pbixray is in the range this module was validated for, and why not if it is not.

    Portable extraction reaches into private pbixray internals (see PRIVATE_TOUCHPOINTS), so it accepts only
    SUPPORTED. `installed` is None when pbixray is missing; `reason` is the message to show a user."""
    try:
        installed = importlib.metadata.version('pbixray')
    except importlib.metadata.PackageNotFoundError:
        return {'installed': None, 'supported': SUPPORTED, 'ok': False,
                'reason': f'pbixray is not installed; portable extraction needs pbixray{SUPPORTED} '
                          '(install the portable extra)'}
    if not accepts(installed):
        return {'installed': installed, 'supported': SUPPORTED, 'ok': False,
                'reason': f'pbixray {installed} is installed, but portable extraction is validated only for '
                          f'pbixray{SUPPORTED} because it relies on private pbixray internals. Install a supported '
                          f'version, or run tests/tools/pbixray_canary.py against {installed} and widen the range '
                          '(docs/portable-extraction.md).'}
    return {'installed': installed, 'supported': SUPPORTED, 'ok': True, 'reason': None}


def available():
    return status()['ok']


# Every private pbixray name this package depends on. tests/test_pbixray_contract.py checks each one against the
# installed pbixray, and tests/tools/pbixray_canary.py against a candidate version.
PRIVATE_TOUCHPOINTS = (
    'pbixray.PBIXRay(path, on_disk=True, temp_dir=...) as a context manager  [portable._metadata]',
    'PBIXRay()._data_model  [portable._metadata]',
    'pbixray.utils.get_data_slice(data_model, "metadata.sqlitedb")  [portable._metadata]',
    'pbixray.loader._FileSink with write(), finish() and the _tmp / _path attributes  [input_limits]',
)


def _metadata(path, temp_dir=None, max_decompressed=None):
    state = status()
    if not state['ok']:
        raise ValueError(state['reason'])
    from pbixray import PBIXRay
    from pbixray.utils import get_data_slice
    path = Path(path)
    limit = max_decompressed or input_limits.decompression_limit(path.stat().st_size, temp_dir)
    # on_disk avoids keeping the entire decompressed model in RAM, and the size cap bounds the temp file.
    # No get_table / iter_table: business rows are never decoded.
    with input_limits.bounded_decompression(limit):
        with PBIXRay(str(path), on_disk=True, temp_dir=temp_dir) as ray:
            raw = get_data_slice(ray._data_model, 'metadata.sqlitedb')
            return input_limits.open_metadata(raw)


def model_document(path, temp_dir=None, max_decompressed=None):
    path = Path(path)
    warnings, gaps = [], []
    with closing(_metadata(path, temp_dir, max_decompressed)) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(name, required=()):
            if name not in names:
                if required:
                    raise ValueError(f'Unsupported Tabular metadata: missing {name}')
                return []
            columns = {r[1] for r in db.execute(f'PRAGMA table_info([{name}])')}
            if set(required) - columns:
                raise ValueError(f'Unsupported Tabular metadata: {name} lacks {sorted(set(required)-columns)}')
            return [dict(r) for r in db.execute(f'SELECT * FROM [{name}]')]
        model_rows = rows('Model', ('Name', 'DefaultMode'))
        if not model_rows:
            raise ValueError('Unsupported Tabular metadata: the Model table is empty')
        raw_model = model_rows[0]
        # SystemFlags=2 includes auto date templates; keep semantic hidden tables,
        # but exclude storage-only H$/R$/U$ engine tables (bit 0).
        tables = {r['ID']: r for r in rows('Table', ('ID', 'Name', 'SystemFlags')) if not r['SystemFlags'] & 1}
        cols = {r['ID']: r for r in rows('Column', ('ID', 'TableID', 'Type', 'SortByColumnID'))
                if r['TableID'] in tables and r['Type'] in (1, 2, 4)}
        def cname(r):
            return r.get('ExplicitName') or r.get('InferredName') or ''
        def group(items, key):
            """{key value: [items]} in the original row order, so each table is a lookup, not a scan of every row."""
            grouped = {}
            for item in items:
                grouped.setdefault(item.get(key), []).append(item)
            return grouped
        def fields(r, mapping):
            return {v: r[k] for k, v in mapping.items() if r.get(k) is not None}
        annotations_by_object = group(rows('Annotation'), 'ObjectID')
        def anns(oid):
            return [{'name': r['Name'], 'value': r['Value']} for r in annotations_by_object.get(oid, [])]
        exprs = {r['ID']: r for r in rows('Expression')}
        formats = {r['ID']: r for r in rows('FormatStringDefinition')}
        detail = {r['ID']: r for r in rows('DetailRowsDefinition')}
        sources = {r['ID']: r for r in rows('DataSource')}
        out_sources = []
        for r in sources.values():
            s = {'name': r['Name']}
            connection = r.get('ConnectionString') or ''
            # Server-created backups can hold encrypted connection strings. Do
            # not emit ciphertext/credentials or invent an endpoint from the BIM.
            # Allowlist redaction: keep identity keys only, so a key we have not heard of never leaks.
            if connection and re.search(r'(?i)(data source|server|provider)\s*=', connection):
                s['connectionString'] = connection_strings.redact(connection, connection_strings.SOURCE_KEYS)
            elif connection:
                warnings.append(f"Data source '{r['Name']}' has an encrypted or opaque connection string. Server/database are unavailable; SQL object names remain visible.")
            if r.get('ConnectionDetails'):
                try:
                    details = json.loads(r['ConnectionDetails'])
                    # Where it points, never how it authenticates.
                    s['connectionDetails'] = {k: details[k] for k in ('protocol', 'address') if isinstance(details, dict) and k in details}
                except (ValueError, TypeError):
                    gaps.append(f"Unreadable connection details for {r['Name']}")
            out_sources.append(s)
        def shared_expression(r):
            out = dict(fields(r, {'Name':'name','Description':'description','LineageTag':'lineageTag'}),
                       kind='m', expression=r.get('Expression') or '')
            if r.get('QueryGroupID') in query_groups:
                out['queryGroup'] = query_groups[r['QueryGroupID']]['Folder']
            if r.get('ID') is not None and anns(r['ID']):
                out['annotations'] = anns(r['ID'])
            return out
        partitions = rows('Partition', ('TableID', 'Type', 'Mode', 'QueryDefinition'))
        # Power Query folders (TOM QueryGroup). Older files have no such table; that is a flat list, not a gap.
        query_groups = {r['ID']: r for r in rows('QueryGroup') if r.get('ID') is not None and r.get('Folder')}
        measures = rows('Measure', ('TableID', 'Name', 'Expression', 'FormatString', 'IsHidden'))
        hierarchies, levels = rows('Hierarchy'), rows('Level')
        calc_groups = {r['ID']: r for r in rows('CalculationGroup')}
        calc_items_by_group = group(rows('CalculationItem'), 'CalculationGroupID')
        cols_by_table, measures_by_table = group(cols.values(), 'TableID'), group(measures, 'TableID')
        partitions_by_table, hierarchies_by_table = group(partitions, 'TableID'), group(hierarchies, 'TableID')
        levels_by_hierarchy = {h: sorted(items, key=lambda x: x.get('Ordinal') or 0)
                               for h, items in group(levels, 'HierarchyID').items()}
        result = []
        for tid, t in tables.items():
            out = fields(t, {'Name':'name','Description':'description','IsHidden':'isHidden',
                             'DataCategory':'dataCategory','LineageTag':'lineageTag'})
            out.update(columns=[], measures=[], partitions=[], hierarchies=[], annotations=anns(tid))
            for c in cols_by_table.get(tid, []):
                col = fields(c, {'SourceColumn':'sourceColumn','Expression':'expression','Description':'description',
                    'IsHidden':'isHidden','IsKey':'isKey','FormatString':'formatString','DisplayFolder':'displayFolder',
                    'DataCategory':'dataCategory','LineageTag':'lineageTag'})
                dt = c.get('ExplicitDataType')
                if dt in (None, 1, 19):
                    dt = c.get('InferredDataType')
                col.update(name=cname(c), dataType=TYPES.get(dt, 'unknown'))
                if dt not in TYPES:
                    gaps.append(f"Unknown data type {dt} on {t['Name']}[{cname(c)}]")
                if c['Type'] == 2:
                    col['type'] = 'calculated'
                if c['SortByColumnID']:
                    sort = cols.get(c['SortByColumnID'])
                    if sort:
                        col['sortByColumn'] = cname(sort)
                    else:
                        gaps.append(f"Unresolved sort key on {t['Name']}[{cname(c)}]")
                out['columns'].append(col)
            for r in measures_by_table.get(tid, []):
                mea = fields(r, {'Name':'name','Expression':'expression','Description':'description',
                    'FormatString':'formatString','IsHidden':'isHidden','DisplayFolder':'displayFolder','LineageTag':'lineageTag'})
                if r.get('FormatStringDefinitionID'):
                    f = formats.get(r['FormatStringDefinitionID'])
                    if f:
                        mea['formatStringDefinition'] = {'expression': f.get('Expression', '')}
                    else:
                        gaps.append(f"Missing dynamic format for {r['Name']}")
                if r.get('DetailRowsDefinitionID') in detail:
                    mea['detailRowsDefinition'] = {'expression': detail[r['DetailRowsDefinitionID']].get('Expression', '')}
                out['measures'].append(mea)
            for r in partitions_by_table.get(tid, []):
                mode = r['Mode']
                if mode == 2:
                    mode = raw_model['DefaultMode']
                mode_name = MODES.get(mode, 'unknown')
                if mode_name in ('unknown', 'default'):
                    gaps.append(f"Unknown effective storage mode on {t['Name']}")
                typ = r['Type']
                query = r.get('QueryDefinition') or ''
                if typ in (1, 2, 4):
                    src = {'type': {1:'query', 2:'calculated', 4:'m'}[typ],
                           'query' if typ == 1 else 'expression': query}
                    if typ == 1 and r.get('DataSourceID') in sources:
                        src['dataSource'] = sources[r['DataSourceID']]['Name']
                elif typ == 7:
                    src = {'type':'calculationGroup'}
                else:
                    # Entity and newer partition encodings need an independently
                    # verified mapping. Never pretend they are M or Direct Lake.
                    src = {'type':'unknown', 'expression':query}
                    gaps.append(f"Partition type {typ} on {t['Name']} is not yet supported by portable extraction; remote-model lineage is incomplete.")
                part = {'name':r['Name'], 'mode':mode_name, 'source':src}
                if r.get('QueryGroupID') in query_groups:
                    part['queryGroup'] = query_groups[r['QueryGroupID']]['Folder']
                out['partitions'].append(part)
            for h in hierarchies_by_table.get(tid, []):
                out['hierarchies'].append({'name':h['Name'], 'levels':[
                    {'name':lv['Name'], 'column':cname(cols[lv['ColumnID']])}
                    for lv in levels_by_hierarchy.get(h['ID'], []) if lv['ColumnID'] in cols]})
            gid = t.get('CalculationGroupID')
            if gid:
                g = calc_groups.get(gid)
                if g:
                    out['calculationGroup'] = {'precedence':g.get('Precedence',0), 'calculationItems':[
                        {'name':ci['Name'], 'expression':ci.get('Expression',''), 'ordinal':ci.get('Ordinal',0)}
                        for ci in calc_items_by_group.get(gid, [])]}
                    gaps.append('Calculation groups are extracted, but advanced selection/format semantics are not yet verified.')
                else:
                    gaps.append(f"Missing calculation group for {t['Name']}")
            result.append(out)
        rels = []
        for r in rows('Relationship'):
            if r['FromTableID'] not in tables or r['ToTableID'] not in tables:
                continue
            if r['FromColumnID'] not in cols or r['ToColumnID'] not in cols:
                gaps.append(f"Unresolved relationship {r['Name']}");continue
            rels.append({'name':r['Name'], 'fromTable':tables[r['FromTableID']]['Name'],
                'toTable':tables[r['ToTableID']]['Name'], 'fromColumn':cname(cols[r['FromColumnID']]),
                'toColumn':cname(cols[r['ToColumnID']]), 'isActive':bool(r['IsActive']),
                'fromCardinality':{1:'one',2:'many'}.get(r['FromCardinality'],'many'),
                'toCardinality':{1:'one',2:'many'}.get(r['ToCardinality'],'one'),
                'crossFilteringBehavior':{1:'oneDirection',2:'bothDirections',3:'automatic'}.get(r['CrossFilteringBehavior'],str(r['CrossFilteringBehavior']))})
        permissions = rows('TablePermission')
        permissions_by_role = group(permissions, 'RoleID')
        raw_roles = rows('Role')
        for r in raw_roles:
            if r.get('ModelPermission') not in PERMISSIONS:
                gaps.append(f"Unknown model permission {r.get('ModelPermission')} for role {r['Name']}")
        roles = [{'name':r['Name'], 'modelPermission':PERMISSIONS.get(r.get('ModelPermission'),'unknown'),
                  'tablePermissions':[{'name':tables[p['TableID']]['Name'], 'filterExpression':p.get('FilterExpression') or ''}
                    for p in permissions_by_role.get(r['ID'], []) if p['TableID'] in tables]}
                 for r in raw_roles]
        if rows('ColumnPermission') or any(p.get('MetadataPermission',0) not in (None,0) for p in permissions):
            gaps.append('Object-level security permissions are present; this renderer does not document them completely.')
        model = {'name':raw_model.get('Name') or path.stem, 'culture':raw_model.get('Culture'), 'tables':result,
                 'relationships':rels, 'dataSources':out_sources, 'roles':roles,
                 'expressions':[shared_expression(r) for r in exprs.values()]}
        if query_groups:
            model['queryGroups'] = [dict(fields(g, {'Folder':'folder','Description':'description'}), annotations=anns(g['ID']))
                                    for g in query_groups.values()]
        if raw_model.get('ID') is not None and anns(raw_model['ID']):
            model['annotations'] = anns(raw_model['ID'])
    # A file cannot establish who else uses a model, nor prove deletion safety.
    coverage = {'backend':'pbixray', 'version':status()['installed'], 'inputKind':path.suffix.lstrip('.').lower(),
                'complete':not gaps, 'warnings':sorted(set(warnings+gaps)),
                'limitations':['Offline snapshot; no refresh, live server inventory or remote-model retrieval.',
                               'Deletion recommendations are disabled for portable extraction until broader dependency parity is verified.']}
    return {'name':path.stem, 'model':model, '_readerWarnings':coverage['warnings']+coverage['limitations'], '_extraction':coverage}


def extract(source, destination):
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    kind = source.suffix.lower()
    if kind not in ('.pbix', '.abf'):
        raise ValueError('Portable extraction accepts PBIX and Tabular ABF files')
    has_model = True
    if kind == '.pbix':
        with zipfile.ZipFile(source) as z:
            has_model = 'DataModel' in z.namelist()
            if 'Report/Layout' in z.namelist():
                info=z.getinfo('Report/Layout')
                if info.file_size > 200*1024*1024:
                    raise ValueError('Report layout exceeds the extraction limit')
                root=destination/'Report';root.mkdir(exist_ok=True)
                (root/'report.json').write_text(z.read(info).decode('utf-16-le').lstrip('\ufeff'),encoding='utf-8')
        from .pbix_batch import extract_pbir
        extract_pbir(source,destination/'Report')
    if has_model:
        document=model_document(source)
        root=destination/'Model';root.mkdir(exist_ok=True)
        (root/'database.json').write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        coverage=document['_extraction']
    else:
        coverage={'backend':'zip','version':'1','inputKind':'pbix','complete':True,'warnings':[],
                  'limitations':['Thin report: the remote semantic model is not embedded.']}
    (destination/'extraction.json').write_text(json.dumps(coverage,indent=2),encoding='utf-8')
    return destination


def load_model(path):
    from .model_parser import parse_model
    with tempfile.TemporaryDirectory() as d:
        target=Path(d)/'model.bim'
        target.write_text(json.dumps(model_document(path)),encoding='utf-8')
        model=parse_model(target)
        model['sourcePath']=str(path)
        return model


def main():
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source');ap.add_argument('destination')
    args=ap.parse_args()
    try:
        extract(args.source,args.destination)
    except Exception as exc:
        ap.exit(1,f'Portable extraction failed: {type(exc).__name__}: {exc}\n')


if __name__=='__main__':
    main()
