"""pbi-tools' default ("folder") model serialization, reassembled into TMSL.

    Model/database.json                 model without tables
    Model/tables/<T>/table.json         table, partitions without expressions (<T>.json in early extracts)
    Model/tables/<T>/table.dax          calculated-table expression
    Model/tables/<T>/columns/<c>.json   (+ <c>.dax for calculated columns)
    Model/tables/<T>/measures/<m>.json  (+ <m>.dax)
    Model/tables/<T>/hierarchies/<h>.json
    Model/queries/<name>.m              M for partitions and shared expressions
    Model/dataSources/<n>/dataSource.json (+ mashup/Formulas/Section1.m) legacy sources

Batch runs ask pbi-tools for the single-file Raw layout; this covers extracts made
with the default settings (PbixProj repositories). Before this, such a model was
read as database.json alone and silently had no tables.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote


def is_folder_model(model_dir: Path) -> bool:
    return (model_dir / 'database.json').is_file() and (model_dir / 'tables').is_dir()


def _table_file(folder: Path) -> Path | None:
    """<T>/table.json, or <T>/<T>.json in early pbi-tools extracts."""
    for path in (folder / 'table.json', folder / f'{folder.name}.json'):
        if path.is_file():
            return path
    return None


def _read(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _text(path: Path):
    return path.read_text(encoding='utf-8-sig') if path.is_file() else None


def _xml_measure(path: Path) -> dict:
    """Early pbi-tools measures: <Measure Name=".."><Expression>..</Expression>..</Measure>."""
    root = ET.fromstring(path.read_text(encoding='utf-8-sig'))
    item = {'name': root.get('Name') or unquote(path.stem)}
    for tag, key in (('Expression', 'expression'), ('FormatString', 'formatString'),
                     ('Description', 'description'), ('DisplayFolder', 'displayFolder')):
        node = root.find(tag)
        if node is not None and node.text is not None:
            item[key] = node.text.strip() if key == 'expression' else node.text
    if root.get('IsHidden', '').lower() == 'true':
        item['isHidden'] = True
    return item


def _objects(folder: Path):
    """Each <name>.json in folder, with the sibling <name>.dax as its expression
    (early extracts: <name>.xml)."""
    if not folder.is_dir():
        return []
    items = [_xml_measure(path) for path in sorted(folder.glob('*.xml'))]
    for path in sorted(folder.glob('*.json')):
        item = _read(path)
        expression = _text(path.with_suffix('.dax'))
        if expression is not None and 'expression' not in item:
            item['expression'] = expression
        items.append(item)
    return items


def assemble(model_dir: str | Path) -> dict:
    model_dir = Path(model_dir)
    database = _read(model_dir / 'database.json')
    model = database.setdefault('model', {})
    queries = {p.stem: p.read_text(encoding='utf-8-sig') for p in (model_dir / 'queries').glob('*.m')} \
        if (model_dir / 'queries').is_dir() else {}
    tables = []
    for folder in sorted(p for p in (model_dir / 'tables').iterdir() if p.is_dir()):
        table_file = _table_file(folder)
        if table_file is None:
            raise ValueError(f'pbi-tools table folder has no table definition: {folder.name}')
        table = _read(table_file)
        name = table.get('name') or folder.name
        table['columns'] = table.get('columns') or _objects(folder / 'columns')
        table['measures'] = table.get('measures') or _objects(folder / 'measures')
        table['hierarchies'] = table.get('hierarchies') or _objects(folder / 'hierarchies')
        table_dax = _text(table_file.with_suffix('.dax'))
        for partition in table.get('partitions') or []:
            source = partition.setdefault('source', {})
            if source.get('expression'):
                continue
            if source.get('type') == 'calculated' and table_dax is not None:
                source['expression'] = table_dax
            elif source.get('type') == 'm':
                expression = queries.get(name) or queries.get(folder.name)
                if expression is not None:
                    source['expression'] = expression
        tables.append(table)
    model['tables'] = tables
    sources = []
    for folder in sorted(p for p in (model_dir / 'dataSources').iterdir() if p.is_dir()) \
            if (model_dir / 'dataSources').is_dir() else []:
        if not (folder / 'dataSource.json').is_file():
            continue
        source = _read(folder / 'dataSource.json')
        section = _text(folder / 'mashup' / 'Formulas' / 'Section1.m')
        if section is not None:
            source['mashupSection'] = section
        sources.append(source)
    if sources and not model.get('dataSources'):
        model['dataSources'] = sources
    for expression in model.get('expressions') or []:
        if not expression.get('expression') and expression.get('name') in queries:
            expression['expression'] = queries[expression['name']]
    return database
