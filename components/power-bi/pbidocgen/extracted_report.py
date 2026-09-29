"""Adapt the legacy report layout to the generator's report contract.

Two containers hold the same legacy layout JSON: pbi-tools' Report/sections
tree (from a PBIX) and the single report.json of a PBIP saved before PBIR.

This is an internal adapter, not a PBIR converter. Legacy/custom visual coverage
is best-effort and explicitly prevents automatic deletion recommendations.
"""
import json
from pathlib import Path
from .report_parser import _SUBQUERY, _collect_aliases, _collect_field_refs, _collect_filters
from .page_references import attach_report_locations


def read_json(path, default=None):
    if not path.exists():
        return default
    for encoding in ('utf-8-sig', 'utf-16'):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except (ValueError, UnicodeError):
            continue
    raise ValueError(f'Unreadable extracted JSON: {path.name}')


def object_json(path, required=False):
    value = read_json(path, None if required else {})
    if not isinstance(value, dict):
        raise ValueError(f'Missing or invalid report object: {path.name}')
    return value


def decode_embedded(node):
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key in {'config', 'query', 'filters', 'dataTransforms'} and isinstance(value, str):
                value = json.loads(value)
            result[key] = decode_embedded(value)
        return result
    if isinstance(node, list):
        return [decode_embedded(value) for value in node]
    return node


def refs(blob, context, subqueries=None):
    # Subquery aliases (q, q1...) declared in the visual's query are read from
    # its config/dataTransforms too; table aliases stay per blob.
    aliases, fields = dict(subqueries or {}), []
    _collect_aliases(blob, aliases)
    _collect_field_refs(blob, aliases, fields, context=context)
    unique = {json.dumps(f, sort_keys=True): f for f in fields}
    return list(unique.values())


def element(folder, filename):
    value = object_json(folder / filename, required=True)
    for key in ('config', 'query', 'filters', 'dataTransforms'):
        part = folder / (key + '.json')
        if part.exists():
            value[key] = read_json(part)
    return decode_embedded(value)


def filters(blob, level, target):
    aliases = {}
    _collect_aliases(blob, aliases)
    return _collect_filters(blob.get('filters'), aliases, level, target)


PBIX_WARNING = ('PBIX report read from the legacy layout. Bookmark page attribution is best-effort; cleanup holds every '
                'field in Review only if a visual\'s data bindings cannot be read.')
LEGACY_WARNING = ('Report is in the legacy single-file layout (report.json). Bookmark page attribution is best-effort; '
                  'cleanup holds every field in Review only if a visual\'s data bindings cannot be read. Save the '
                  'project with the PBIR format enabled for full coverage.')


def _visual(visual, label, fallback_id):
    vc = visual.get('config') or {}
    if not isinstance(vc, dict):
        raise ValueError(f'Visual config must be an object: {fallback_id}')
    single = vc.get('singleVisual') or vc.get('singleVisualGroup') or {}
    vid = str(vc.get('name') or fallback_id)
    vtype = single.get('visualType') or ('group' if 'singleVisualGroup' in vc else 'unknown')
    layouts = vc.get('layouts') or []
    position = dict((layouts[0].get('position') or {}) if layouts else {})
    position.update({k: visual[k] for k in ('x', 'y', 'width', 'height') if k in visual})
    title = None
    for obj in (single.get('vcObjects') or single.get('objects') or {}).get('title', []):
        literal = obj.get('properties', {}).get('text', {}).get('expr', {}).get('Literal', {}).get('Value')
        if isinstance(literal, str):
            title = literal.strip("'")
    vf = filters(visual, 'visual', f'{label} / {title or vtype}')
    # Alias maps stay within each query/config blob instead of leaking
    # from one visual/query into another.
    fields, subqueries = [], {}
    _collect_aliases(visual, subqueries)
    subqueries = {k: v for k, v in subqueries.items() if v == _SUBQUERY}
    for key, blob in visual.items():
        if isinstance(blob, (dict, list)):
            fields.extend(refs(blob, 'visual ' + key, subqueries))
    if vtype == 'qnaVisual':
        # Q&A answers are re-derived from the question at runtime.
        for f in fields:
            f['runtime'] = True
    # A data query we could not turn into fields: its bindings are unknown.
    unreadable = not fields and bool(visual.get('query') or visual.get('dataTransforms'))
    return vf, dict(id=vid, type=vtype, title=title, hidden=bool(vc.get('isHidden', False)), fields=fields,
                    filters=vf, unreadableBindings=unreadable, **{k: position.get(k) for k in ('x', 'y', 'width', 'height')})


def _build(report, sections, bookmarks, name, warning):
    """report: decoded report blob; sections: [(page blob, [(visual blob, fallback id)], fallback id)]."""
    config = report.get('config') or {}
    if not isinstance(config, dict):
        raise ValueError('Report config must be an object')
    pages, page_ids = [], set()
    sections = sorted(sections, key=lambda item: item[0].get('ordinal', 0))
    for index, (page, visual_items, fallback) in enumerate(sections):
        pid = str(page.get('name') or fallback)
        if pid in page_ids:
            raise ValueError(f'Duplicate extracted page identity: {pid}')
        page_ids.add(pid)
        label = str(page.get('displayName') or pid)
        pc = page.get('config') or {}
        if not isinstance(pc, dict):
            raise ValueError(f'Page config must be an object: {label}')
        page_filters = filters(page, 'page', label)
        visuals, visual_ids = [], set()
        for visual, vfallback in visual_items:
            vf, v = _visual(visual, label, vfallback)
            if v['id'] in visual_ids:
                raise ValueError(f'Duplicate visual identity on page {label}: {v["id"]}')
            visual_ids.add(v['id'])
            page_filters.extend(vf)
            visuals.append(v)
        pages.append(dict(id=pid, name=label, hidden=pc.get('visibility') in (1, 'HiddenInViewMode', 'hidden')
                          or page.get('visibility') in (1, 'HiddenInViewMode', 'hidden'),
                          isActive=index == config.get('activeSectionIndex', 0), width=page.get('width'),
                          height=page.get('height'), visuals=visuals, filters=page_filters,
                          otherFields=refs({k: v for k, v in page.items() if k != 'visualContainers'}, 'page expression')))
    if not pages:
        raise ValueError('No report pages were extracted; refusing to produce an apparently complete report')
    return attach_report_locations(dict(name=name, pages=pages, reportFilters=filters(report, 'report', '(entire report)'),
        otherFields=refs({k: v for k, v in report.items() if k != 'sections'}, 'report expression'),
        bookmarks=bookmarks, manifest=[], legacyLayout=True, warnings=[{
            'severity': 'warning', 'category': 'Legacy report layout', 'message': warning}]))


def parse_extracted_report(folder, name):
    """pbi-tools' split Report/ folder (from a PBIX)."""
    root = Path(folder)
    report = element(root, 'report.json')
    sections = root / 'sections'
    if not sections.is_dir():
        raise ValueError('Extracted report has no sections directory; report format is unsupported. Try saving as PBIP/PBIR.')
    items = []
    for p in sorted(sections.iterdir()):
        if not p.is_dir():
            continue
        visual_dir = p / 'visualContainers'
        visuals = [(element(vp, 'visualContainer.json'), vp.name)
                   for vp in (sorted(visual_dir.iterdir()) if visual_dir.exists() else []) if vp.is_dir()]
        items.append((element(p, 'section.json'), visuals, p.name))
    bookmarks = []
    bookmark_dir = root / 'bookmarks'
    # pbi-tools writes each bookmark as a folder: bookmark.json plus one file
    # per visual state. Group those files into one bookmark; references stay at
    # report scope (no page attribution is invented).
    roots = {p.parent for p in bookmark_dir.rglob('bookmark.json')} if bookmark_dir.exists() else set()
    # A top-level folder without any bookmark.json is still one bookmark.
    roots |= {d for d in (bookmark_dir.iterdir() if bookmark_dir.exists() else [])
              if d.is_dir() and not any(r == d or d in r.parents for r in roots)}
    roots = sorted(roots)
    for folder in roots:
        nested = [r for r in roots if r != folder and folder in r.parents]
        files = [p for p in sorted(folder.rglob('*.json')) if not any(n in p.parents for n in nested)]
        head = decode_embedded(read_json(folder / 'bookmark.json') or {})
        fields = {}
        for path in files:
            for f in refs(decode_embedded(read_json(path)), 'bookmark'):
                fields[json.dumps(f, sort_keys=True)] = f
        bname = head.get('displayName') if isinstance(head, dict) else None
        bookmarks.append(dict(name=str(bname or folder.relative_to(bookmark_dir)), fields=list(fields.values())))
    return _build(report, items, bookmarks, name, PBIX_WARNING)


def _layout_bookmarks(config):
    out = []
    def walk(items):
        for b in items or []:
            if not isinstance(b, dict):
                continue
            if b.get('children'):
                walk(b['children'])  # a bookmark group
            else:
                out.append(dict(name=str(b.get('displayName') or b.get('name') or 'Bookmark'), fields=refs(b, 'bookmark')))
    walk(config.get('bookmarks') if isinstance(config, dict) else [])
    return out


def is_legacy_layout(path):
    """True for a PBIP report folder holding a single legacy report.json."""
    path = Path(path)
    candidate = path / 'report.json'
    if not candidate.is_file() or (path / 'definition').is_dir():
        return False
    value = read_json(candidate, {})
    return isinstance(value, dict) and isinstance(value.get('sections'), list)


def parse_legacy_layout(path, name):
    """A PBIP report folder (or a report.json/Layout file) in the legacy single-file layout."""
    path = Path(path)
    layout = read_json(path / 'report.json' if path.is_dir() else path)
    if not isinstance(layout, dict) or not isinstance(layout.get('sections'), list):
        raise ValueError('Legacy report layout has no sections')
    layout = decode_embedded(layout)
    items = []
    for index, section in enumerate(layout['sections']):
        if not isinstance(section, dict):
            raise ValueError('Report section must be an object')
        section = decode_embedded(section)
        visuals = [(decode_embedded(v), f'visual-{i}') for i, v in enumerate(section.get('visualContainers') or [])
                   if isinstance(v, dict)]
        items.append((section, visuals, f'section-{index}'))
    return _build(layout, items, _layout_bookmarks(layout.get('config') or {}), name, LEGACY_WARNING)
