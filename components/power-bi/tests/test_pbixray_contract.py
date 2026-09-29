"""The private pbixray internals we rely on, and the range of versions we accept.

Portable extraction reaches into private pbixray names (portable.PRIVATE_TOUCHPOINTS) and accepts only a validated
range of pbixray versions. These tests fail loudly, on every build, if the installed pbixray no longer matches that
contract or if the range has drifted between the places it is written down. tests/tools/pbixray_canary.py runs
the same checks against a candidate version before the range is widened.
"""
import importlib.metadata
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

from pbidocgen import portable

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
import pbixray_canary  # noqa: E402


def declared_in(path, pattern):
    match = re.search(pattern, Path(path).read_text(encoding='utf-8'))
    return match.group(1) if match else None


class PrivateContract(unittest.TestCase):
    def test_the_installed_pbixray_is_in_the_supported_range(self):
        state = portable.status()
        self.assertTrue(state['ok'], state['reason'])

    def test_every_private_touchpoint_and_behaviour_still_holds(self):
        failures = [(name, detail) for name, ok, detail in pbixray_canary.run(lift_version_gate=False) if not ok]
        self.assertEqual(failures, [],
                         'pbixray changed something portable extraction relies on. Update portable.py / '
                         'input_limits.py, then change the range in every place (see docs/portable-extraction.md).')

    def test_the_touchpoints_are_listed_for_the_next_maintainer(self):
        self.assertGreaterEqual(len(portable.PRIVATE_TOUCHPOINTS), 4)
        self.assertTrue(all('[' in item for item in portable.PRIVATE_TOUCHPOINTS), 'each names the code that uses it')


class TheRangeIsWrittenDownConsistently(unittest.TestCase):
    def test_every_declaration_agrees_with_the_code(self):
        declared = {
            'components/power-bi/pyproject.toml': declared_in(ROOT / 'components/power-bi/pyproject.toml',
                                                               r'portable\s*=\s*\["pbixray([^"]+)"\]'),
            'apps/generator/pyproject.toml': declared_in(ROOT / 'apps/generator/pyproject.toml',
                                                          r'pbixray\s*=\s*\["pbixray([^"]+)"\]'),
        }
        self.assertEqual(declared, {path: portable.SUPPORTED for path in declared})

    def test_the_lock_file_pins_one_version_inside_the_range(self):
        locked = declared_in(ROOT / 'requirements-lock.txt', r'(?m)^pbixray==(\S+)$')
        self.assertIsNotNone(locked, 'requirements-lock.txt must pin pbixray exactly')
        self.assertTrue(portable.accepts(locked), f'locked pbixray {locked} is outside {portable.SUPPORTED}')

    def test_the_range_text_matches_the_numbers_the_gate_uses(self):
        low, high = (".".join(map(str, part)) for part in (portable.SUPPORTED_MIN, portable.SUPPORTED_BELOW))
        self.assertEqual(portable.SUPPORTED, f'>={low},<{high}')

    def test_no_other_source_file_hardcodes_the_range(self):
        offenders = []
        for folder in ('components/power-bi/pbidocgen', 'apps/generator/bidoc_generator'):
            for path in sorted((ROOT / folder).rglob('*.py')):
                if path.name == 'portable.py':
                    continue                                  # the one place that defines it
                if portable.SUPPORTED in path.read_text(encoding='utf-8'):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], 'use portable.SUPPORTED / portable.status(), not the literal range')


class WhichVersionsAreAccepted(unittest.TestCase):
    def test_the_validated_releases_and_later_patches(self):
        for version in ('0.15.0', '0.15.1', '0.15.5', '0.15.6', '0.15.99'):
            self.assertTrue(portable.accepts(version), version)

    def test_everything_else_is_refused(self):
        for version in ('0.14.9', '0.16.0', '1.0.0', '0.15', '0.15.6rc1', '0.15.6.dev1', '0.15.6.post1', 'abc', ''):
            self.assertFalse(portable.accepts(version), version)


class GateMessages(unittest.TestCase):
    def test_an_unsupported_version_is_named_and_the_way_forward_given(self):
        with mock.patch('importlib.metadata.version', return_value='0.16.0'):
            state = portable.status()
        self.assertEqual((state['ok'], state['installed']), (False, '0.16.0'))
        for expected in ('0.16.0', portable.SUPPORTED, 'private', 'pbixray_canary.py'):
            self.assertIn(expected, state['reason'])
        self.assertNotIn('not installed', state['reason'])       # it is installed, just not supported

    def test_a_missing_pbixray_is_reported_as_missing(self):
        with mock.patch('importlib.metadata.version', side_effect=importlib.metadata.PackageNotFoundError):
            state = portable.status()
        self.assertEqual((state['ok'], state['installed']), (False, None))
        self.assertIn('not installed', state['reason'])

    def test_a_supported_older_patch_is_accepted_and_recorded_as_installed(self):
        with mock.patch('importlib.metadata.version', return_value='0.15.2'):
            self.assertEqual(portable.status(), {'installed': '0.15.2', 'supported': portable.SUPPORTED,
                                                 'ok': True, 'reason': None})

    def test_extraction_stops_before_reading_anything_on_an_unsupported_version(self):
        with mock.patch('importlib.metadata.version', return_value='0.16.0'):
            with self.assertRaisesRegex(ValueError, 'validated only for'):
                portable.model_document(pbixray_canary.FIXTURE)


if __name__ == '__main__':
    unittest.main()
