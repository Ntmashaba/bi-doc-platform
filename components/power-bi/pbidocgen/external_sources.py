"""Documented non-SQL access functions. Static identity extraction, never I/O.

Reference: https://learn.microsoft.com/en-us/powerquery-m/accessing-data-functions
See CONNECTOR-COVERAGE.md for supported forms and limits.
"""
import copy
import re
from urllib.parse import unquote
from urllib.parse import urlsplit, urlunsplit

from .source_labels import refine_source_type

# Function -> external type, navigation behavior. Document readers are separate:
# Excel.Workbook(Json.Document etc.) is not a new connection.
EXTERNAL = {
    'File.Contents': ('File', 'file'),
    'Folder.Files': ('Folder', 'files'), 'Folder.Contents': ('Folder', 'hierarchy'),
    'SharePoint.Files': ('SharePoint files', 'files'),
    'SharePoint.Contents': ('SharePoint files', 'hierarchy'),
    'SharePoint.Tables': ('SharePoint list', 'lists'),
    'Web.Contents': ('Web / API', 'endpoint'),
    'Web.BrowserContents': ('Web / API', 'endpoint'), 'Web.Headers': ('Web / API', 'endpoint'),
    'OData.Feed': ('OData', 'endpoint'),
    'AzureStorage.BlobContents': ('Azure Blob Storage', 'file'),
    'AzureStorage.Blobs': ('Azure Blob Storage', 'hierarchy'),
    'AzureStorage.DataLake': ('Azure Data Lake', 'files'),
    'AzureStorage.DataLakeContents': ('Azure Data Lake', 'file'),
}
READERS = {'Excel.Workbook', 'Csv.Document', 'Json.Document', 'Xml.Document',
           'Xml.Tables', 'Pdf.Tables', 'Access.Database', 'Html.Table', 'Web.Page',
           'Binary.Buffer', 'Binary.Decompress', 'Text.FromBinary', 'Lines.FromBinary'}


def safe_location(location):
    """Keep endpoint/path identity; omit URL credentials, queries and fragments."""
    if not re.match(r'^https?://', location, re.I):
        return location, []
    try:
        parts = urlsplit(location)
        clean = urlunsplit((parts.scheme, parts.netloc.rsplit('@', 1)[-1], parts.path, '', ''))
        notes = ['URL credentials, query parameters or fragment omitted from source identity'] if clean != location else []
        return clean, notes
    except ValueError:
        return '', ['URL could not be parsed']


def join_location(base, name):
    separator = '\\' if '\\' in base and not base.startswith(('http:', 'https:')) else '/'
    return base.rstrip('/\\') + separator + name.lstrip('/\\') if base else ''


def external_value(tracer, fn, args):
    from .m_sources import Value, union
    result = union(args)
    kind, mode = EXTERNAL[fn]
    location = args[0].text if args and args[0].kind == 'text' else ''
    location, notes = safe_location(location or '')
    opts = args[1].members if len(args) > 1 else {}
    if fn == 'Web.Contents':
        relative = opts.get('RelativePath')
        if relative is not None:
            if relative.kind == 'text':
                location, extra = safe_location(join_location(location, relative.text))
                notes += extra
            else:
                notes.append('RelativePath is dynamic; only the base endpoint is known')
        if 'Query' in opts:
            notes.append('Request query parameters omitted; endpoint identity only')
    if not location:
        notes.append('External location is dynamic or unresolved')
    # Shared vocabulary with partition parsing: a SharePoint document read
    # through Web.Contents is a file, and File.Contents is named by its type.
    kind = refine_source_type(kind, location)
    if kind == 'SharePoint file':
        mode = 'file'
    if mode in {'files', 'hierarchy', 'lists'}:
        notes.append('Collection location identified; individual item selection unresolved')
    obj = re.split(r'[/\\]', location.rstrip('/\\'))[-1] if mode == 'file' and location else ''
    if obj and location.lower().startswith(('http://', 'https://')):
        obj = unquote(obj)  # SLA%20Targets.xlsx -> SLA Targets.xlsx
    if mode == 'endpoint':
        obj = location
    conn = dict(sourceType=kind, server=location, database='', schema='', location=location,
                primaryQuery=tracer.current_query, navigationMode=mode)
    result.connections.append(conn)
    result.objects.append(dict(conn, object=obj, sourceKind=mode, sql='', notes=notes, evidence='External access function: '+fn))
    result.kind = 'table'
    return result


def navigate_external(base, fields):
    """Return None for relational navigation; preserve external document identity."""
    if not base.connections or not any(c.get('navigationMode') for c in base.connections):
        return None
    result = copy.deepcopy(base)
    if len(base.connections) != 1:
        result.issues.append('External item navigation has multiple possible inputs')
        return result
    conn = base.connections[0]
    mode = conn.get('navigationMode')
    if mode in {'file', 'document', 'endpoint'}:
        # A worksheet, JSON field or OData entity is not another connection.
        return result
    name = fields.get('name') or fields.get('id') or fields.get('title')
    if not name:
        result.issues.append('External item selection unresolved')
        return result
    folder = fields.get('folder path', '')
    base_location = folder or conn.get('location', '')
    location = join_location(base_location, name) if mode != 'lists' else conn.get('location', '')
    location, notes = safe_location(location)
    if mode == 'files' and not folder:
        # Flat lists can contain the same filename in several subfolders.
        location = conn.get('location', '')
        notes.append('Filename identified but its containing folder is unresolved')
    updated = dict(conn, location=location, server=location)
    if mode in {'files', 'hierarchy'}:
        updated['sourceType'] = refine_source_type(conn['sourceType'], location)
    # Hierarchical Content can be either a file or another folder. Preserve the
    # selected path; a document reader will close navigation at the file boundary.
    result.connections = [updated]
    result.objects = [dict(updated, object=name, sourceKind='list' if mode=='lists' else 'selected item',
                           sql='', notes=notes, evidence='External item navigation')]
    return result
