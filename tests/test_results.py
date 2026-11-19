"""base/pihole-sync/results.py — the outcome of a run, and the closed lists its metrics carry."""

import unittest

import logs
from pihole_client import PiholeError
from results import ALL_CHANGES, Error, ErrorCode, SyncResult, classify


def pihole_error(status):
    return PiholeError("x", method="GET", route="/api/groups", status=status)


class Codes(unittest.TestCase):
    def test_every_code_is_a_valid_label_value(self):
        # A new value is a change to the naming convention's accepted values.
        self.assertEqual(
            {str(c) for c in ErrorCode},
            {"config", "auth", "network", "pihole_api", "gravity", "terminated", "unknown"},
        )

    def test_a_pihole_error_is_classified_by_its_status(self):
        for status, code in [(None, "network"), (401, "auth"), (403, "auth"), (404, "pihole_api"), (503, "pihole_api")]:
            with self.subTest(status=status):
                self.assertEqual(classify(pihole_error(status)), code)

    def test_every_change_series_is_listed_once(self):
        self.assertEqual(len(set(ALL_CHANGES)), 6 * 3)
        self.assertNotIn("kind", ALL_CHANGES[0].attributes)  # a group has no kind


class Result(unittest.TestCase):
    def test_no_errors_is_ok_and_any_error_makes_it_a_failure(self):
        result = SyncResult()
        self.assertTrue(result.ok)
        logs.logger.disabled = True
        try:
            result.add_error(Error(ErrorCode.PIHOLE_API, "boom"))
        finally:
            logs.logger.disabled = False
        self.assertFalse(result.ok)
