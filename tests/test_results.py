"""base/pihole-sync/results.py — the outcome of a run, and the closed list of error codes."""

import unittest

import logs
import results


class Errors(unittest.TestCase):
    def test_every_code_is_a_valid_label_value(self):
        self.assertEqual(
            {str(c) for c in results.ErrorCode}, {"config", "auth", "network", "pihole_api", "gravity", "unknown"}
        )

    def test_every_retriable_failure_says_why_a_restart_can_fix_it(self):
        self.assertTrue(set(results.RETRIABLE) <= set(results.ErrorCode))
        self.assertTrue(all(reason.strip() for reason in results.RETRIABLE.values()))
        # What a restart can't fix exits 0 (ADR 0010).
        for code in (results.ErrorCode.CONFIG, results.ErrorCode.AUTH, results.ErrorCode.UNKNOWN):
            self.assertNotIn(code, results.RETRIABLE)

    def test_classify(self):
        self.assertEqual(results.classify(results.SyncError(results.ErrorCode.AUTH, "x")), "auth")
        self.assertEqual(results.classify(OSError("refused")), "network")
        self.assertEqual(results.classify(KeyError("groups")), "unknown")

    def test_a_result_with_no_errors_is_ok_and_any_error_makes_it_a_failure(self):
        result = results.Result()
        self.assertTrue(result.ok)
        logs.logger.disabled = True
        try:
            result.fail(results.ErrorCode.PIHOLE_API, "boom")
        finally:
            logs.logger.disabled = False
        self.assertFalse(result.ok)
