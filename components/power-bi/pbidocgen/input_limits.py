"""Limits for reading untrusted PBIX / ABF files. No shell execution, no network.

pbixray decompresses the whole DataModel (compressed table data as well as metadata) into a temporary
file before anything is parsed, with no size limit. A crafted file can therefore fill the disk. These
helpers bound that, and open the small embedded metadata database read-only and defensively.

Real files decompress to about 2-3x their size (VertiPaq data is already compressed) and their metadata
database is 0.3-1.6 MB, so the defaults leave a wide margin for legitimate models.
"""
import contextlib
import os
import shutil
import sqlite3
import tempfile

MIB, GIB = 1 << 20, 1 << 30
DECOMPRESSED_FLOOR = 256 * MIB      # always allowed, even for a tiny input
DECOMPRESSED_RATIO = 50             # decompressed bytes allowed per input byte
DECOMPRESSED_CEILING = 16 * GIB
DISK_SHARE = 0.9                    # never plan to use more than this share of the free space
METADATA_MAX = 512 * MIB
ENV_LIMIT = 'BIDOC_MAX_DECOMPRESSED_BYTES'


class LimitExceeded(ValueError):
    """Untrusted input is larger or more complex than the configured limits allow."""


def decompression_limit(input_size, temp_dir=None):
    """Most bytes a DataModel may decompress to: by ratio, capped, and never beyond the free disk space."""
    override = os.environ.get(ENV_LIMIT)
    if override:
        try:
            limit = int(override)
        except ValueError:
            raise ValueError(f'{ENV_LIMIT} must be a whole number of bytes') from None
        if limit <= 0:
            raise ValueError(f'{ENV_LIMIT} must be positive')
    else:
        limit = min(DECOMPRESSED_CEILING, max(DECOMPRESSED_FLOOR, DECOMPRESSED_RATIO * int(input_size)))
    free = shutil.disk_usage(temp_dir or tempfile.gettempdir()).free
    return min(limit, int(free * DISK_SHARE))


def _describe(n):
    if n >= GIB:
        return f'{n / GIB:.1f} GiB'
    return f'{n / MIB:.0f} MiB' if n >= MIB else f'{n} bytes'


@contextlib.contextmanager
def bounded_decompression(limit):
    """While active, pbixray's temporary decompression file may not grow beyond `limit` bytes.

    It swaps pbixray's private `loader._FileSink` for a counting subclass. If that hook is missing (a different
    pbixray than a validated one) this refuses to run: never fall back to unbounded decompression."""
    from pbixray import loader
    original = getattr(loader, '_FileSink', None)
    if original is None or not callable(getattr(original, 'write', None)) or not hasattr(original, 'finish'):
        raise ValueError('Cannot bound decompression with this pbixray version; refusing to read the file. '
                         'Install a supported pbixray version (portable.SUPPORTED).')

    class Bounded(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._written = 0

        def write(self, chunk):
            self._written += len(chunk)
            if self._written > limit:
                self._discard()
                raise LimitExceeded(f'The data model decompresses to more than {_describe(limit)}; refusing to '
                                    f'read it. (Set {ENV_LIMIT} to raise the limit for a trusted file.)')
            return super().write(chunk)

        def _discard(self):
            """Remove the partly written temp file: pbixray only cleans up after a successful finish()."""
            with contextlib.suppress(OSError):
                self._tmp.close()
            with contextlib.suppress(OSError):
                os.unlink(self._path)

    loader._FileSink = Bounded
    try:
        yield
    finally:
        loader._FileSink = original


_ALLOWED_PRAGMAS = {'table_info'}


def _read_only_authorizer(action, arg1, arg2, dbname, source):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in _ALLOWED_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def open_metadata(raw, max_bytes=METADATA_MAX):
    """An in-memory, read-only, SELECT-only connection over untrusted metadata.sqlitedb bytes.

    Analysis Services metadata is plain tables and indexes. Anything else (views, triggers, virtual tables) can
    run code or unbounded work when queried, so it is refused. Untrusted schemas are not trusted, and the
    authorizer allows only SELECT and table_info."""
    if len(raw) > max_bytes:
        raise LimitExceeded(f'The metadata database is {_describe(len(raw))}, over the {_describe(max_bytes)} limit')
    db = sqlite3.connect(':memory:')
    try:
        db.deserialize(bytes(raw))
        db.execute('PRAGMA trusted_schema=OFF')
        for kind, name, sql in db.execute("SELECT type, name, COALESCE(sql, '') FROM sqlite_master").fetchall():
            if kind not in ('table', 'index') or sql.lstrip().upper().startswith('CREATE VIRTUAL'):
                raise ValueError(f'Unsupported Tabular metadata: {kind} {name!r} is not a plain table or index')
        db.execute('PRAGMA query_only=ON')
        db.set_authorizer(_read_only_authorizer)
        db.row_factory = sqlite3.Row
        return db
    except BaseException:
        db.close()
        raise
