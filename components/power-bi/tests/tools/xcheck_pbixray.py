"""Cross-check the pbixray extractor against pbixray itself and the raw report layout.

    pip install pbixray
    python tests/tools/xcheck_pbixray.py PATH [PATH ...]     (PBIX files or folders; exit 1 on any mismatch)

For each PBIX: extract with pbidocgen.pbixray_extract, load through the normal pipeline, then compare
table, column, measure and relationship counts with pbixray's own tables, and page/visual counts with
the PBIX's raw Report/Layout. The model counts read the same pbixray data the extractor uses, so they test
the conversion and our parsing, not pbixray. Developer tool; not run by the unit-test suite.
"""
import json
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings('ignore')

from pbidocgen.linker import link  # noqa: E402
from pbidocgen.pbix_batch import load_extracted  # noqa: E402
from pbidocgen.pbixray_extract import INTERNAL_PREFIXES, extract_pbix, read_layout  # noqa: E402
from pbidocgen.renderer import build_payload  # noqa: E402


def _names(frame, column):
    return list(frame[column]) if column in frame.columns else []


def check(path):
    """Return (summary dict, list of mismatch descriptions)."""
    from pbixray import PBIXRay
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        has_model = 'DataModel' in archive.namelist()
    with tempfile.TemporaryDirectory() as work:
        extract_pbix(path, Path(work) / 'x', None, 0, Path(work) / 'log.txt')
        model, report = load_extracted(Path(work) / 'x', path, has_model)
    payload = build_payload(model, report, link(model, report) if model else None, path.stem)
    summary = {'mode': payload['mode'], 'pages': len(report['pages']),
               'visuals': sum(len(p['visuals']) for p in report['pages'])}
    problems = []
    layout = read_layout(path)
    if isinstance(layout, dict):
        expect = (len(layout.get('sections', [])), sum(len(s.get('visualContainers', [])) for s in layout.get('sections', [])))
        if expect != (summary['pages'], summary['visuals']):
            problems.append(f"pages/visuals {(summary['pages'], summary['visuals'])} != layout {expect}")
    if model:
        ray = PBIXRay(str(path))
        keep = lambda n: not str(n).startswith(INTERNAL_PREFIXES)  # noqa: E731
        tables = {n for n in _names(ray.tmschema_partitions, 'TableName') + _names(ray.dax_measures, 'TableName')
                  + _names(ray.tmschema_tables, 'Name') if keep(n)}
        columns = [1 for _, c in ray.tmschema_columns.iterrows() if int(c['Type']) != 3 and keep(c['TableName'])]
        ours = {'tables': len(model['tables']), 'columns': sum(len(t['columns']) for t in model['tables']),
                'measures': len(model['measures']), 'relationships': len(model['relationships'])}
        theirs = {'tables': len(tables), 'columns': len(columns), 'measures': len(ray.dax_measures),
                  'relationships': len(ray.relationships)}
        summary.update(ours)
        problems += [f'{k}: ours {ours[k]} != pbixray {theirs[k]}' for k in ours if ours[k] != theirs[k]]
    return summary, problems


def main(argv):
    files = []
    for arg in argv:
        p = Path(arg)
        files += sorted(p.rglob('*.pbix')) if p.is_dir() else [p]
    failed = 0
    for f in files:
        try:
            summary, problems = check(f)
        except Exception as exc:
            print(f'ERR  {f.name}: {type(exc).__name__}: {str(exc)[:100]}')
            failed += 1
            continue
        print(f"{'DIFF' if problems else 'OK  '} {f.name}  {json.dumps(summary)}")
        for problem in problems:
            print('      ' + problem)
        failed += bool(problems)
    print(f'{len(files) - failed} of {len(files)} agree')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
