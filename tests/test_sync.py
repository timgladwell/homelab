"""base/pihole-sync/sync.py — main() hands the run's Result to the metrics, however the run ends."""

import unittest

import logs
import metrics
import results
import sync


class ObservedRun(unittest.TestCase):
    """main() hands the run's Result to the metrics, however the run ends."""

    def setUp(self):
        self.real_run = sync._run
        self.real_record = metrics.Metrics.record
        logs.logger.disabled = True

    def tearDown(self):
        logs.logger.disabled = False
        sync._run = self.real_run
        metrics.Metrics.record = self.real_record

    def run_main(self, run):
        """Run main() around `run`; return the Result it recorded."""
        sync._run = run
        recorded = []
        metrics.Metrics.record = lambda me, result: recorded.append(result)
        # A raised exception is the Job exiting non-zero, so keep it to assert on.
        self.raised = None
        try:
            sync.main()
        except Exception as exc:  # noqa: BLE001 - whichever it is, the test inspects it
            self.raised = exc
        self.assertEqual(len(recorded), 1, "the metrics must be recorded exactly once")
        return recorded[0]

    def test_a_clean_run_is_ok(self):
        self.assertTrue(self.run_main(lambda result: None).ok)

    def test_the_job_exits_non_zero_only_when_a_restart_can_fix_the_failure(self):
        # ADR 0010: a non-zero exit is "retry me", never "I failed". Every code is
        # checked, so a new one can't be added without being decided.
        for code in results.ErrorCode:
            with self.subTest(code=str(code)):

                def run(result, code=code):
                    raise results.SyncError(code, "boom")

                result = self.run_main(run)
                self.assertEqual(self.raised is not None, code in results.RETRIABLE)
                # Either way the run is reported as the failure it was.
                self.assertFalse(result.ok)
                self.assertEqual([e.code for e in result.errors], [code])

    def test_an_error_on_one_object_exits_zero(self):
        def run(result):
            result.fail(results.ErrorCode.PIHOLE_API, "adding domain 'x' failed")

        result = self.run_main(run)
        self.assertIsNone(self.raised)
        self.assertFalse(result.ok)

    def test_an_error_along_the_way_makes_the_run_a_failure(self):
        def run(result):
            result.fail(results.ErrorCode.PIHOLE_API, "adding domain 'x' failed")

        result = self.run_main(run)
        self.assertFalse(result.ok)
        self.assertEqual([e.fatal for e in result.errors], [False])

    def test_a_fatal_error_reports_a_code_from_the_closed_list(self):
        cases = [
            (results.SyncError(results.ErrorCode.AUTH, "no"), "auth"),
            (results.SyncError(results.ErrorCode.GRAVITY, "no"), "gravity"),
            (OSError("connection refused"), "network"),
            (KeyError("groups"), "unknown"),
        ]
        for exc, code in cases:
            with self.subTest(code=code):

                def run(result, exc=exc):
                    raise exc

                (error,) = self.run_main(run).errors
                self.assertEqual((error.code, error.fatal), (code, True))
                self.assertEqual(error.exception, type(exc).__name__)

    def test_what_was_changed_before_a_fatal_error_is_still_reported(self):
        def run(result):
            result.change("domain", "added", "block")
            raise results.SyncError(results.ErrorCode.PIHOLE_API, "GET /api/clients failed")

        result = self.run_main(run)
        self.assertEqual(result.changes[("domain", "block", "added")], 1)
        self.assertFalse(result.ok)
