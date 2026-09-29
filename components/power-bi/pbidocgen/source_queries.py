"""Power Query inventory for the three-column Sources CSV.

Query inventory covers the complete supplied model, independently of report-page
usage: shared functions, parameters and staging expressions may feed many pages.
"""


def build_source_queries(model: dict | None, report: dict | None) -> list[dict]:
    if not model:
        return []
    report_name = (report or {}).get('name') or model.get('name') or 'Not supplied'
    rows = []
    for table in model.get('tables', []):
        partitions = table.get('partitions', [])
        for part in partitions:
            if part.get('type', '').lower() != 'm':
                continue
            name = table['name'] if len(partitions) == 1 else f"{table['name']} / {part['name']}"
            rows.append({'report': report_name, 'queryName': name, 'mCode': part.get('expression') or ''})
    for expression in model.get('expressions', []):
        if expression.get('kind', '').lower() == 'm':
            rows.append({'report': report_name, 'queryName': expression['name'],
                         'mCode': expression.get('expression') or ''})
    return sorted(rows, key=lambda row: (row['queryName'].casefold(), row['mCode']))
