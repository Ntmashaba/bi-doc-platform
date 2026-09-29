"""Does the installed pbixray still work with portable extraction? Run before moving the pin.

    python components/power-bi/tests/tools/pbixray_canary.py        (exit 1 if any check fails)

Portable extraction reaches into private pbixray internals (portable.PRIVATE_TOUCHPOINTS) and accepts only a
validated range of pbixray versions (portable.SUPPORTED). This tool checks every touchpoint, then reads a real
public PBIX and compares the result with values captured from the validated versions, so a change in behaviour,
not only a renamed attribute, is caught. It lifts the version gate for the run so a candidate outside the range
can be checked.

The unit test tests/test_pbixray_contract.py runs the same checks against the installed version on every build.
A scheduled workflow (.github/workflows/pbixray-canary.yml) runs this against the newest release.
"""
import collections
import importlib.metadata
import inspect
import os
import sqlite3
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))                       # components/power-bi
FIXTURE = HERE.parents[4] / 'apps' / 'generator' / 'tests' / 'fixtures' / 'dp500-08-composite.pbix'

# Captured from the validated versions on the fixture (DP-500 lab 08, a composite model). Identical for 0.15.0-0.15.5.
GOLDEN = {
    'tables': ['DateTableTemplate_dff96084-cd3e-40b8-86fd-3a856e997fe5', 'Order Date', 'Product', 'Sales',
               'Sales Territory', 'Targets'],
    'measures': 2, 'columns': 42, 'relationships': 5,
    'modes': {'directQuery': 4, 'import': 2},
    'parameters': ['SqlServerInstance', 'SqlServerDatabase', 'Culture'],
}


def bomb_stream(mib):
    """A DataModel stream of `mib` MiB of zeros, Xpress9-compressed as one continuous stream (a few KiB)."""
    from xpress9 import Xpress9
    x, chunk = Xpress9(), bytes(1 << 20)
    parts = ["This backup was created using XPress9 compression.".encode('utf-16le') + b'\0\0']
    for _ in range(mib):
        packed = x.compress(chunk, len(chunk))
        parts.append(len(chunk).to_bytes(4, 'little') + len(packed).to_bytes(4, 'little') + packed)
    return b''.join(parts)


def _touchpoints():
    from pbixray import PBIXRay, loader, utils
    signature = inspect.signature(PBIXRay.__init__).parameters
    missing = [name for name in ('on_disk', 'temp_dir') if name not in signature]
    assert not missing, f'PBIXRay.__init__ lost the parameter(s) {missing}'
    assert hasattr(PBIXRay, '__enter__') and hasattr(PBIXRay, '__exit__'), 'PBIXRay is no longer a context manager'
    assert callable(getattr(utils, 'get_data_slice', None)), 'pbixray.utils.get_data_slice is gone'
    sink_class = getattr(loader, '_FileSink', None)
    assert sink_class is not None, 'pbixray.loader._FileSink is gone'
    assert callable(getattr(sink_class, 'write', None)) and callable(getattr(sink_class, 'finish', None)), \
        '_FileSink lost write() or finish()'
    with tempfile.TemporaryDirectory() as tmp:
        sink = sink_class(tmp)
        try:
            assert hasattr(sink._tmp, 'close'), '_FileSink._tmp is no longer a file object'
            assert isinstance(sink._path, str) and os.path.exists(sink._path), '_FileSink._path is no longer the temp file path'
        finally:
            sink._tmp.close()
            os.unlink(sink._path)


def _data_model_bridge():
    from pbixray import PBIXRay
    from pbixray.utils import get_data_slice
    with tempfile.TemporaryDirectory() as tmp:
        with PBIXRay(str(FIXTURE), on_disk=True, temp_dir=tmp) as ray:
            assert hasattr(ray, '_data_model'), 'PBIXRay()._data_model is gone'
            raw = bytes(get_data_slice(ray._data_model, 'metadata.sqlitedb'))
        assert raw.startswith(b'SQLite format 3\x00'), 'metadata.sqlitedb is no longer a SQLite database'
        assert os.listdir(tmp) == [], f'temp files left after closing: {os.listdir(tmp)}'


def _metadata_schema():
    from pbixray import PBIXRay
    from pbixray.utils import get_data_slice
    from pbidocgen import input_limits
    with PBIXRay(str(FIXTURE), on_disk=True) as ray:
        db = input_limits.open_metadata(get_data_slice(ray._data_model, 'metadata.sqlitedb'))
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        need = {'Model', 'Table', 'Column', 'Partition', 'Measure', 'Relationship', 'DataSource'}
        assert need <= tables, f'metadata tables missing: {sorted(need - tables)}'
    finally:
        db.close()


def _real_file_matches_golden():
    from pbidocgen import portable
    model = portable.model_document(FIXTURE)['model']
    got = {'tables': sorted(t['name'] for t in model['tables']),
           'measures': sum(len(t['measures']) for t in model['tables']),
           'columns': sum(len(t['columns']) for t in model['tables']),
           'relationships': len(model['relationships']),
           'modes': dict(collections.Counter(p['mode'] for t in model['tables'] for p in t['partitions'])),
           'parameters': [e['name'] for e in model['expressions']]}
    differences = {k: (GOLDEN[k], got[k]) for k in GOLDEN if got[k] != GOLDEN[k]}
    assert not differences, f'output differs from the validated versions (expected, got): {differences}'


def _size_limit_still_enforced():
    from pbidocgen import input_limits, portable
    with tempfile.TemporaryDirectory() as tmp:
        bomb = Path(tmp) / 'bomb.pbix'
        with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('DataModel', bomb_stream(24))
        work = Path(tmp) / 'work'
        work.mkdir()
        try:
            portable.model_document(bomb, temp_dir=str(work), max_decompressed=8 << 20)
        except input_limits.LimitExceeded:
            pass
        except Exception as exc:
            raise AssertionError(f'the size cap did not stop a 24 MiB decompression bomb at an 8 MiB limit (it was fully '
                                 f'decompressed, then failed with {type(exc).__name__}): pbixray no longer writes through '
                                 'loader._FileSink, so the cap is not enforced') from exc
        else:
            raise AssertionError('a 24 MiB decompression bomb was not refused at an 8 MiB limit: the size cap no longer works')
        assert os.listdir(work) == [], f'partial temp file left behind: {os.listdir(work)}'


CHECKS = (
    ('private names and shapes exist', _touchpoints),
    ('data-model bridge returns the metadata database', _data_model_bridge),
    ('metadata schema has the tables we read', _metadata_schema),
    ('real file reads identically to the validated versions', _real_file_matches_golden),
    ('decompression size cap still enforced', _size_limit_still_enforced),
)


def run(lift_version_gate=True):
    """[(name, ok, detail)] for every check. With lift_version_gate, any installed pbixray is accepted for the run."""
    from pbidocgen import portable
    if lift_version_gate:
        portable.accepts = lambda version: True
    results = []
    for name, check in CHECKS:
        try:
            check()
            results.append((name, True, ''))
        except Exception as exc:                                     # report every failure, not just the first
            results.append((name, False, f'{type(exc).__name__}: {exc}'))
    return results


def main():
    installed = importlib.metadata.version('pbixray')
    from pbidocgen import portable
    in_range = portable.accepts(installed)                           # before run() lifts the gate
    results = run()
    lines = [f'pbixray {installed} installed; portable extraction is validated for pbixray{portable.SUPPORTED}'
             + (' (inside the range)' if in_range else ' (OUTSIDE the range: this is a candidate check)')]
    for name, ok, detail in results:
        lines.append(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f'\n        {detail}' if detail else ''))
    failed = [r for r in results if not r[1]]
    lines.append(f'{len(results) - len(failed)} of {len(results)} checks pass' +
                 ('' if not failed else f'. pbixray {installed} is NOT compatible: fix portable.py / input_limits.py, '
                                        'then change the supported range (docs/portable-extraction.md).'))
    print('\n'.join(lines))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as out:
            out.write('### pbixray canary\n\n```\n' + '\n'.join(lines) + '\n```\n')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
