"""Validate shapes at file boundaries before extraction traverses them."""
import json


def validate_report(value):
    if not isinstance(value, dict):
        raise ValueError('Expected a report JSON object')
    # Keys that must be objects wherever they appear (query expressions, which
    # the parser follows recursively) and keys that only mean "visual part" at
    # the top of a visual.json. Elsewhere the same words are plain metadata:
    # Desktop writes "reportVersionAtImport": {"visual": "2.12.0", ...}.
    expression_objects = {'Expression', 'SourceRef', 'Column', 'Measure', 'HierarchyLevel', 'Aggregation'}
    top_objects = {'visual', 'position', 'query', 'queryState', 'objects'}
    def walk(node, parent='', depth=0):
        if isinstance(node, dict):
            for key, child in list(node.items()):
                if key == 'Expression' and parent == 'NativeVisualCalculation' and isinstance(child, str):
                    continue  # a visual calculation's DAX text, not a model field reference
                if key in expression_objects or (depth == 0 and key in top_objects) \
                        or (key == 'Hierarchy' and parent == 'Expression'):
                    if not isinstance(child, dict):
                        raise ValueError(f'{key} must be an object')
                if key in {'Entity', 'Source', 'Property', 'Level'} and not isinstance(child, str):
                    raise ValueError(f'{key} must be text')
                if key == 'Name' and 'Entity' in node and not isinstance(child, str):
                    raise ValueError('Alias Name must be text')
                if key == 'filterConfig':
                    if isinstance(child, str):
                        child = json.loads(child)
                        node[key] = child
                    if child is not None and not isinstance(child, (dict, list)):
                        raise ValueError('filterConfig must be an object or array')
                if key == 'filters' and parent == 'filterConfig':
                    if not isinstance(child, list) or not all(isinstance(f, dict) for f in child):
                        raise ValueError('filters must be an array of objects')
                walk(child, key, depth + 1)
        elif isinstance(node, list):
            for child in node:
                walk(child, parent, depth + 1)
    walk(value)
    return value


def validate_model(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get('model', doc), dict):
        raise ValueError('Semantic model must be a JSON object')
    collections = {'tables', 'columns', 'measures', 'partitions', 'relationships', 'roles',
                   'tablePermissions', 'hierarchies', 'levels', 'expressions', 'calculationItems',
                   'annotations', 'extendedProperties', 'dataSources'}
    objects = {'source', 'calculationGroup', 'detailRowsDefinition', 'formatStringDefinition'}
    # Free-form payloads (annotation/extended-property values, linguistic
    # schemas) may contain any keys, including a non-text "name".
    free_form = {'value', 'content', 'linguisticMetadata', 'changedProperties'}
    identity = {'name', 'table', 'column', 'fromTable', 'toTable', 'fromColumn', 'toColumn'}
    def walk(node, is_root=False):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in free_form:
                    continue
                if key in collections and (not isinstance(value, list) or not all(isinstance(v, dict) for v in value)):
                    raise ValueError(f'Model {key} must be an array of objects')
                if key in objects and value is not None and not isinstance(value, dict):
                    raise ValueError(f'Model {key} must be an object')
                if key in identity and not isinstance(value, str) and not (is_root and key == 'name' and value is None):
                    raise ValueError(f'Model {key} must be text')
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(doc.get('model', doc), is_root=True)
