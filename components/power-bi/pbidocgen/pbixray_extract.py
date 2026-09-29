"""Compatibility entry points for the earlier PBIXRay integration.

All extraction delegates to the version-pinned portable reader. The platform
normally invokes pbidocgen.portable in a child process with a timeout.
"""
import json
import sys
import zipfile
from pathlib import Path

from .portable import extract, model_document


def build_model(path):
    with zipfile.ZipFile(path) as archive:
        if 'DataModel' not in archive.namelist():
            return None
    return model_document(path)


def read_layout(path):
    with zipfile.ZipFile(path) as archive:
        try:
            raw = archive.read('Report/Layout')
        except KeyError:
            return None
    for encoding in ('utf-16-le', 'utf-8-sig'):
        try:
            return json.loads(raw.decode(encoding))
        except (ValueError, UnicodeError):
            continue
    return None


def extract_pbix(source, destination, tool, timeout, log):
    from .pbix_batch import extract_pbix as child_extract
    child_extract(source, destination, 'pbixray', timeout or 600, Path(log))
    # Older callers expect this filename. Avoid duplicate model definitions.
    model = Path(destination) / 'Model' / 'database.json'
    if model.exists():
        model.rename(model.with_name('model.bim'))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 4 or args[0] != 'extract' or '-extractFolder' not in args:
        print('usage: pbixray_extract extract FILE -extractFolder DIR', file=sys.stderr)
        return 2
    try:
        folder = Path(args[args.index('-extractFolder') + 1])
        extract(args[1], folder)
        model = folder / 'Model' / 'database.json'
        if model.exists():
            model.rename(model.with_name('model.bim'))
    except Exception as exc:
        print(f'error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
