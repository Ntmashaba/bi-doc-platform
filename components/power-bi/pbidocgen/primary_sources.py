"""External source identities, grouped by report page, without embedded code."""
from .m_sources import Tracer, materialize
from .source_objects import source_definitions


def build_primary_sources(model, report, source_objects, analysis_issues=None, table_issues=None):
    """analysis_issues block every absence-of-usage verdict; table_issues only
    block the model table they name."""
    table_issues = table_issues or {}
    if not model:
        return dict(rows=[], unresolved=[])
    candidates = list(source_objects)
    reached = {name for row in source_objects for name in row.get('referencedQueries', [])}
    reached.update(name for row in source_objects for name in row.get('primaryQueries', []))
    # Shared queries with no traced model consumer also belong in the inventory.
    # Parameters alone are not external sources, and no page usage is invented.
    tracer = Tracer(source_definitions(model))
    known = {(r.get('sourceType'), r.get('location') or r.get('server')) for r in source_objects}
    report_name = report['name'] if report else 'Not supplied'
    for expression in model.get('expressions', []):
        name = expression['name']
        if expression.get('kind', '').lower() != 'm' or name in reached:
            continue
        value = tracer.named(name)
        if value.kind in {'text', 'literal'} and not value.connections and not value.objects:
            continue
        for item in materialize(value):
            if (item.get('sourceType'), item.get('location') or item.get('server')) in known:
                # Helper queries (Combine files' Sample File / Transform Sample File)
                # re-read a source a model table already lists.
                continue
            candidates.append(dict(item, report=report_name, page='', pageId='',
                                   pageScope='No model consumer resolved', pageUsage='Not assessed',
                                   queryName=name, table=''))

    groups, unresolved = {}, {}
    for item in candidates:
        if item['status'] == 'Not applicable':
            continue
        origins = item.get('primaryQueries', [])
        # Unrecognised connectors are named but still count as unresolved coverage.
        if not origins or item['sourceType'] == 'Unknown' or item['sourceType'].endswith('(unrecognised connector)'):
            key = (item['report'], item['pageId'], item['pageScope'], item['queryName'])
            unresolved[key] = {k: item[k] for k in ('report', 'page', 'pageId', 'pageScope', 'queryName')}
            # Unknown inputs remain in the metadata export as coverage rows.
        identity = ('report', 'page', 'pageId', 'pageScope', 'sourceType', 'server', 'database', 'schema', 'object', 'location')
        values = tuple(item.get(k, '') for k in identity)
        key = values + ((item['queryName'] if not origins else ''),)
        if key not in groups:
            groups[key] = dict(zip(identity, values), primaryQueries=set(), consumingQueries=set(),
                               tables=set(), pageUsage=set(), statuses=set(), dependencyStatus=set(),
                               reportingStatuses=set(), usageEvidence=set(), preparationEffects=set(),
                               definitionQueries=set(), storageModes=set())
        row = groups[key]
        row['primaryQueries'].update(origins)
        if item.get('table'):
            row['tables'].add(item['table'])
            row['consumingQueries'].add(item['queryName'])
        row['pageUsage'].add(item['pageUsage'])
        row['statuses'].add(item['status'])
        row['definitionQueries'].add(item['queryName'])
        if item.get('table'):
            row['storageModes'].add(item.get('storageMode') or 'Not supplied')
        row['preparationEffects'].update(item.get('preparationEffects', []))
        dependency = ('Model partition defined' if item['queryName'] in origins else 'Preparation dependency to model') if item.get('table') else 'Defined only; no model consumer found'
        if item.get('table') and not origins:
            dependency = 'Model partition defined; source path unresolved'
        row['dependencyStatus'].add(dependency)
        if not origins or not report:
            usage = 'Usage unresolved'
            evidence = 'External source or report metadata is insufficient to establish reporting usage'
        elif item.get('pageId'):
            kinds = item.get('pageKinds') or [item['pageUsage']]
            possible_only = all(k.startswith('Possible') for k in kinds)
            usage = 'Possible model dependency' if possible_only else 'Potential reporting dependency'
            evidence = 'Downstream model table has a possible dependency on this page' if possible_only else 'Downstream model table is referenced on this page; contribution of this external input is not proven'
        elif item['pageScope'] == 'Bookmark/report scope only':
            usage, evidence = 'Report scope only', 'Report/bookmark dependency found without a resolved individual page'
        elif analysis_issues or table_issues.get(item.get('table')) or item['status'] != 'Resolved':
            usage, evidence = 'Usage unresolved', 'Incomplete analysis prevents a reliable absence-of-usage assessment'
        elif not item.get('table'):
            usage, evidence = 'No model consumer found', 'No traced model partition consumes this shared query; execution is not observed'
        elif item['pageUsage'] == 'No page usage detected':
            usage, evidence = 'No reporting usage found', 'Model partition exists but no reporting dependency was detected in the supplied extract'
        else:
            usage, evidence = 'Usage unresolved', 'No resolved page evidence is available'
        row['reportingStatuses'].add(usage)
        row['usageEvidence'].add(evidence)
    rows = []
    for row in groups.values():
        statuses = row.pop('statuses')
        row['status'] = 'Unresolved' if 'Unresolved' in statuses else 'Partial' if 'Partial' in statuses else 'Resolved'
        priorities = ['Usage unresolved', 'Potential reporting dependency', 'Possible model dependency', 'Report scope only', 'No reporting usage found', 'No model consumer found']
        statuses_seen = row.pop('reportingStatuses')
        row['reportingStatus'] = next(s for s in priorities if s in statuses_seen)
        row['usageConfidence'] = 'Possible' if row['pageId'] and row['reportingStatus'] != 'Usage unresolved' else 'Not established'
        row['runtimeStatus'] = 'Not observed; load and refresh execution unknown'
        row['removalAssessment'] = 'No deletion verdict; review dependencies'
        for key in ('primaryQueries', 'consumingQueries', 'tables', 'pageUsage', 'dependencyStatus', 'usageEvidence', 'preparationEffects', 'definitionQueries', 'storageModes'):
            row[key] = sorted(row[key])
        rows.append(row)
    return dict(rows=sorted(rows, key=lambda r: tuple(r[k] for k in ('report', 'pageId', 'pageScope', 'sourceType', 'server', 'database', 'schema', 'object'))),
                unresolved=[unresolved[k] for k in sorted(unresolved)])
