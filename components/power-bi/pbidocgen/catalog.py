"""Offline documentation catalogue and portable, non-secret metadata."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import quote

METADATA_ID = 'pbi-documentation-metadata'


def validate_metadata(value):
    if not isinstance(value, dict):
        raise ValueError('Documentation metadata must be an object')
    allowed = {'reportLocation', 'folder', 'connections'}
    if set(value) - allowed:
        raise ValueError('Unsupported documentation fields; only reportLocation, folder and connections are accepted')
    result = {}
    for key in ('reportLocation', 'folder'):
        text = value.get(key, '')
        if not isinstance(text, str):
            raise ValueError(f'{key} must be text')
        result[key] = text
    connections = value.get('connections', [])
    if not isinstance(connections, list):
        raise ValueError('connections must be a list')
    fields = {'sourceType', 'server', 'database', 'username', 'authentication', 'connectionName'}
    result['connections'] = []
    for connection in connections:
        if not isinstance(connection, dict) or set(connection) - fields:
            raise ValueError('Connection metadata accepts identity, username and authentication fields only; no passwords or tokens')
        if any(not isinstance(v, str) for v in connection.values()):
            raise ValueError('Connection fields must be text')
        result['connections'].append({key: connection.get(key, '') for key in sorted(fields)})
    return result


class _MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metadata = None
        self.capture = False
        self.text = ''
        self.is_hub = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') == 'pbi-documentation-hub':
            self.is_hub = True
        if tag == 'script' and attrs.get('id') == METADATA_ID:
            self.capture = True
            self.text = ''

    def handle_data(self, data):
        if self.capture:
            self.text += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.capture:
            self.metadata = validate_metadata(json.loads(self.text))
            self.capture = False


def read_metadata(text):
    parser = _MetadataParser()
    parser.feed(text)
    return parser.metadata


def json_script(value):
    return json.dumps(value, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def describe_html(text, filename):
    parser = _MetadataParser()
    parser.feed(text)
    if parser.is_hub:
        return None
    title = Path(filename).stem
    generated = ''
    pbix_source = ''
    mode = ''
    summary = {}
    match = re.search(r'const DATA\s*=\s*', text)
    if match:
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[match.end():].replace('<\\/', '</'))
            if isinstance(payload, dict):
                title = str(payload.get('title') or title)
                generated = str(payload.get('generated') or '')
                pbix_source = str(payload.get('pbixSource') or '')
                mode = str(payload.get('mode') or '')
                summary = clean_summary(payload.get('summary'))
        except (ValueError, TypeError):
            pass
    return dict(title=title, filename=filename, generated=generated, metadata=parser.metadata or {},
                pbixSource=pbix_source, mode=mode, summary=summary)


def clean_summary(value):
    """Keep only the known, plain-text summary fields a report may carry."""
    if not isinstance(value, dict):
        return {}
    text = lambda v: v if isinstance(v, str) else ''
    number = lambda v: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
    counts = value.get('counts') if isinstance(value.get('counts'), dict) else {}
    sources = []
    for s in value.get('sources') or []:
        if isinstance(s, dict):
            sources.append({k: text(s.get(k)) for k in ('label', 'sourceType', 'server', 'database', 'location')}
                           | {'tables': [t for t in s.get('tables') or [] if isinstance(t, str)]})
    return {'counts': {k: number(counts.get(k)) for k in ('tables', 'columns', 'measures', 'pages', 'visuals')},
            'sources': sources,
            'coverageIssues': number(value.get('coverageIssues')),
            'deletionCandidates': number(value.get('deletionCandidates')),
            'needsReview': number(value.get('needsReview')),
            'measuresDescribed': number(value.get('measuresDescribed')),
            'duplicateMeasureSets': number(value.get('duplicateMeasureSets'))}


def build_catalog(folder, output=None):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError(f'Catalogue folder not found: {folder}')
    output = Path(output) if output else folder / 'pbi-home.html'
    rows = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if path.suffix.lower() not in {'.html', '.htm'} or not path.is_file() or path.resolve() == output.resolve():
            continue
        try:
            row = describe_html(path.read_text(encoding='utf-8-sig'), path.name)
            if row:
                row['href'] = quote(path.name, safe='')
                rows.append(row)
        except (ValueError, UnicodeError) as exc:
            rows.append(dict(title=path.stem, filename=path.name, href=quote(path.name, safe=''),
                             metadata={}, generated='', warning=f'Metadata could not be read: {exc}'))
    # Never replace an unrelated user-authored HTML file.
    if output.exists() and 'name="pbi-documentation-hub"' not in output.read_text(encoding='utf-8-sig'):
        raise ValueError(f'Refusing to replace non-catalogue file: {output}')
    template = Path(__file__).with_name('catalog.html').read_text(encoding='utf-8')
    template = template.replace('/*__CATALOG__*/[]', json_script(rows))
    batch_path = folder / 'pbix-batch-results.json'
    batch = None
    if batch_path.exists():
        try:
            value = json.loads(batch_path.read_text(encoding='utf-8'))
            if isinstance(value, dict) and isinstance(value.get('files'), list):
                batch = value
        except (ValueError, OSError):
            pass
    template = template.replace('/*__BATCH__*/null', json_script(batch))
    output.write_text(template, encoding='utf-8')
    return output
