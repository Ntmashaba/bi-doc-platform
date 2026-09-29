"""One source per partition, from the M tracer (review item A4, parser merge).

model_parser's regex patterns only see a partition's own M text, so a
partition that reads a shared query or parameter came out as "Unknown" on
Overview, Lineage and Tables while Sources (the tracer) resolved it. This pass
traces every M partition through the model's shared queries and replaces the
regex result whenever the tracer knows more. The regex result is kept when the
tracer finds nothing, so no partition loses information it had before.
"""
from __future__ import annotations

from .m_sources import Tracer, materialize, INTERNAL_SOURCES
from .source_labels import source_label

_RANK = {'Resolved': 0, 'Partial': 1, 'Not applicable': 2, 'Unresolved': 3}


def _known(row):
    return row.get('sourceType') not in (None, '', 'Unknown')


def apply_traced_sources(model: dict) -> None:
    from .source_objects import source_definitions  # avoid an import cycle
    tracer = Tracer(source_definitions(model))
    for table in model.get('tables', []):
        parts = table.get('partitions', [])
        for part in parts:
            if part.get('type') == 'entity' and part['source'].get('sourceType') == 'DirectQuery (entity)':
                name = (part['source'].get('expressionSource') or '').strip().strip("'")
                rows = [r for r in materialize(tracer.named(name)) if _known(r)] if name else []
                if rows:
                    best = min(rows, key=lambda r: _RANK.get(r['status'], 9))
                    entity = part['source'].get('object')
                    part['source'] = dict(part['source'], sourceType=best['sourceType'],
                                          server=best.get('server') or None, database=best.get('database') or None,
                                          object=entity or best.get('object') or None, traceStatus=best['status'],
                                          tracedFrom=[name])
                    part['source']['label'] = source_label(part['source'])
                continue
            if part.get('type') != 'm':
                continue
            query_name = table['name'] if len(parts) == 1 else table['name'] + ' / ' + part['name']
            rows = [r for r in materialize(tracer.trace(part.get('expression') or '', query_name)) if _known(r)]
            if not rows:
                continue
            # Stable: among equally resolved rows keep the tracer's order, so a
            # native query's first FROM table (not the alphabetically first) leads.
            rows.sort(key=lambda r: _RANK.get(r['status'], 9))
            best = rows[0]
            current = part['source']
            regex_known = current.get('sourceType') not in (None, 'Unknown')
            if regex_known and ((best['status'] == 'Unresolved' and best['sourceType'] not in INTERNAL_SOURCES)
                                or best['sourceType'].endswith('(unrecognised connector)')):
                continue  # the regex result is at least as specific
            location = best.get('location') or ''
            updated = dict(current,
                           sourceType=best['sourceType'],
                           server=best.get('server') or None,
                           database=best.get('database') or None,
                           schema=best.get('schema') or None,
                           object=best.get('object') or None,
                           detail=location or current.get('detail'),
                           nativeQuery=bool(best.get('sql')) or bool(current.get('nativeQuery')),
                           traceStatus=best['status'],
                           tracedFrom=sorted(set(best.get('primaryQueries') or [])))
            if best['sourceType'] in INTERNAL_SOURCES:
                updated.update(server=None, database=None, schema=None, object=None, detail=None)
            updated['label'] = source_label(updated)
            others = []
            for r in rows[1:]:
                label = source_label(dict(r, detail=r.get('location')))
                if label != updated['label'] and label not in others:
                    others.append(label)
            if others:
                updated['otherSources'] = others
            part['source'] = updated
