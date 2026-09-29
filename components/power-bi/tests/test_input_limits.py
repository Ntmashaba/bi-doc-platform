"""Untrusted PBIX / ABF input: bounded decompression and a read-only, defensive metadata database."""
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from pbidocgen import input_limits, portable
from pbidocgen.input_limits import LimitExceeded

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
from pbixray_canary import bomb_stream  # noqa: E402  (shared with the pbixray canary)

# A real public DirectQuery + Import PBIX (DP-500 lab 08), kept as a fixture by the generator tests.
SAMPLE = Path(__file__).resolve().parents[3] / 'apps' / 'generator' / 'tests' / 'fixtures' / 'dp500-08-composite.pbix'


def database(*statements):
    db = sqlite3.connect(':memory:')
    for statement in statements:
        db.execute(statement)
    db.commit()
    raw = db.serialize()
    db.close()
    return raw


class DecompressionLimit(unittest.TestCase):
    def setUp(self):
        self.disk = mock.patch('shutil.disk_usage', return_value=mock.Mock(free=10 * input_limits.GIB * 100))
        self.disk.start()
        self.addCleanup(self.disk.stop)
        os.environ.pop(input_limits.ENV_LIMIT, None)
        self.addCleanup(os.environ.pop, input_limits.ENV_LIMIT, None)

    def test_floor_ratio_and_ceiling(self):
        mib = input_limits.MIB
        self.assertEqual(input_limits.decompression_limit(55 * 1024), 256 * mib)              # tiny input: the floor
        self.assertEqual(input_limits.decompression_limit(100 * mib), 50 * 100 * mib)         # by ratio
        self.assertEqual(input_limits.decompression_limit(10 * input_limits.GIB), input_limits.DECOMPRESSED_CEILING)

    def test_never_more_than_a_share_of_the_free_disk(self):
        with mock.patch('shutil.disk_usage', return_value=mock.Mock(free=1000 * input_limits.MIB)):
            # 100 MiB in would allow 5000 MiB by ratio; only 90% of the 1000 MiB free may be planned for
            self.assertEqual(input_limits.decompression_limit(100 * input_limits.MIB),
                             int(1000 * input_limits.MIB * input_limits.DISK_SHARE))

    def test_environment_override(self):
        os.environ[input_limits.ENV_LIMIT] = str(5 * input_limits.MIB)
        self.assertEqual(input_limits.decompression_limit(10 ** 9), 5 * input_limits.MIB)
        for bad in ('lots', '0', '-1'):
            os.environ[input_limits.ENV_LIMIT] = bad
            with self.assertRaises(ValueError):
                input_limits.decompression_limit(1)


class BoundedDecompression(unittest.TestCase):
    def test_real_file_reads_normally_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            document = portable.model_document(SAMPLE, temp_dir=tmp)
            self.assertEqual(len(document['model']['tables']), 6)
            self.assertEqual(os.listdir(tmp), [])

    def test_over_the_limit_is_refused_and_the_partial_temp_file_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LimitExceeded) as caught:
                portable.model_document(SAMPLE, temp_dir=tmp, max_decompressed=1024)
            self.assertIn('decompresses to more than 1024 bytes', str(caught.exception))
            self.assertEqual(os.listdir(tmp), [])

    def test_environment_limit_reaches_the_reader(self):
        os.environ[input_limits.ENV_LIMIT] = '2048'
        self.addCleanup(os.environ.pop, input_limits.ENV_LIMIT, None)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LimitExceeded):
                portable.model_document(SAMPLE, temp_dir=tmp)

    def test_the_hook_is_restored_afterwards_even_after_a_refusal(self):
        from pbixray import loader
        original = loader._FileSink
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(LimitExceeded):
            portable.model_document(SAMPLE, temp_dir=tmp, max_decompressed=1024)
        self.assertIs(loader._FileSink, original)
        with input_limits.bounded_decompression(10):
            self.assertIsNot(loader._FileSink, original)
        self.assertIs(loader._FileSink, original)

    def test_refuses_to_run_unbounded_when_the_hook_is_missing(self):
        from pbixray import loader
        with mock.patch.object(loader, '_FileSink', None):
            with self.assertRaises(ValueError) as caught:
                with input_limits.bounded_decompression(10):
                    self.fail('must not run without a way to bound decompression')
        self.assertIn('Cannot bound decompression', str(caught.exception))


class DecompressionBomb(unittest.TestCase):
    """A few KiB of input that claims a huge data model. Before the limit, pbixray wrote all of it to disk
    (600 MiB from 5 KiB in a manual run) and left the temp file behind after failing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / 'work'
        self.work.mkdir()
        os.environ.pop(input_limits.ENV_LIMIT, None)
        self.addCleanup(os.environ.pop, input_limits.ENV_LIMIT, None)

    def pbix(self, mib):
        path = Path(self.tmp.name) / 'bomb.pbix'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('DataModel', bomb_stream(mib))
        return path

    def test_the_bomb_really_is_tiny_and_would_expand(self):
        self.assertLess(self.pbix(64).stat().st_size, 64 * 1024)

    def test_pbix_bomb_is_refused_early_and_leaves_no_temp_file(self):
        with self.assertRaises(LimitExceeded):
            portable.model_document(self.pbix(96), temp_dir=str(self.work), max_decompressed=32 * input_limits.MIB)
        self.assertEqual(os.listdir(self.work), [])

    def test_the_default_limit_applies_without_any_argument(self):
        os.environ[input_limits.ENV_LIMIT] = str(48 * input_limits.MIB)
        with self.assertRaises(LimitExceeded):
            portable.model_document(self.pbix(96), temp_dir=str(self.work))
        self.assertEqual(os.listdir(self.work), [])

    def test_a_raw_abf_bomb_is_refused_too(self):
        path = Path(self.tmp.name) / 'bomb.abf'
        path.write_bytes(bomb_stream(96))
        with self.assertRaises(LimitExceeded):
            portable.model_document(path, temp_dir=str(self.work), max_decompressed=32 * input_limits.MIB)
        self.assertEqual(os.listdir(self.work), [])


class HardenedMetadata(unittest.TestCase):
    PLAIN = ('CREATE TABLE Model (Name, DefaultMode)', "INSERT INTO Model VALUES ('M', 0)")

    def test_plain_tables_are_readable(self):
        db = input_limits.open_metadata(database(*self.PLAIN, 'CREATE INDEX i ON Model(Name)'))
        self.assertEqual(db.execute('SELECT Name FROM Model').fetchone()['Name'], 'M')
        self.assertEqual([r['name'] for r in db.execute('PRAGMA table_info([Model])')], ['Name', 'DefaultMode'])
        db.close()

    def test_views_and_triggers_are_refused(self):
        for extra in ('CREATE VIEW Measure AS SELECT * FROM Model',
                      'CREATE TRIGGER t AFTER INSERT ON Model BEGIN SELECT 1; END'):
            with self.assertRaisesRegex(ValueError, 'not a plain table or index'):
                input_limits.open_metadata(database(*self.PLAIN, extra))

    def test_a_virtual_table_schema_is_refused(self):
        build = sqlite3.connect(':memory:')
        build.execute('CREATE TABLE Model (Name)')
        build.execute('PRAGMA writable_schema=ON')
        build.execute("INSERT INTO sqlite_master VALUES ('table', 'v', 'v', 0, 'CREATE VIRTUAL TABLE v USING fts5(a)')")
        build.commit()
        raw = build.serialize()
        build.close()
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            input_limits.open_metadata(raw)

    def test_nothing_can_be_written_or_attached(self):
        db = input_limits.open_metadata(database(*self.PLAIN))
        for statement in ('CREATE TABLE x (a)', "INSERT INTO Model VALUES ('N', 1)", 'DELETE FROM Model',
                          "ATTACH DATABASE ':memory:' AS other", 'PRAGMA writable_schema=ON',
                          'DROP TABLE Model', 'CREATE TEMP TABLE t (a)'):
            with self.assertRaises(sqlite3.DatabaseError, msg=statement):
                db.execute(statement)
        self.assertEqual(len(db.execute('SELECT * FROM Model').fetchall()), 1)      # unchanged
        with self.assertRaises(sqlite3.DatabaseError):                              # no SQL functions at all
            db.execute('SELECT count(*) FROM Model')
        db.close()

    def test_an_oversized_metadata_database_is_refused(self):
        with self.assertRaises(LimitExceeded):
            input_limits.open_metadata(database(*self.PLAIN), max_bytes=100)


if __name__ == '__main__':
    unittest.main()
