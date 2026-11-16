"""base/pihole-sync/sync.py — the run's lifecycle: which failures exit non-zero, and that every run is reported.

ADR 0010: a non-zero exit means "retry me", never "I failed". Whether the run
succeeded is in the result handed to the metrics.
"""

import os
import pathlib
import signal
import tempfile
import unittest
from http import HTTPStatus

import logs
import metrics
import sync
from pihole_client import PiholeError
from results import Action, ErrorCode, Object


class FakeClient:
    """Stands in for PiholeClient: `fail_on` names the method that raises `error`."""

    error = None
    fail_on = None
    instances = []

    def __init__(self, url, password, metrics):
        self.closed = False
        FakeClient.instances.append(self)

    def _maybe_fail(self, where):
        if FakeClient.fail_on == where:
            raise FakeClient.error

    def __enter__(self):
        self._maybe_fail("login")
        return self

    def __exit__(self, *exc_info):
        self.closed = True

    def run_gravity(self):
        self._maybe_fail("gravity")


class Run(unittest.TestCase):
    def setUp(self):
        logs.logger.disabled = True
        self.dir = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.dir.name)
        (d / "config.yaml").write_text("groups: []\n")
        (d / "clients.yaml").write_text("clients: []\n")
        self.patches = {
            (sync, "CONFIG_PATH"): str(d / "config.yaml"),
            (sync, "CLIENTS_PATH"): str(d / "clients.yaml"),
            (sync, "STATE_DIR"): str(d / "state"),
            (sync, "_GRAVITY_FLAG"): str(d / "state" / "gravity.flag"),
            (sync, "PiholeClient"): FakeClient,
            (sync.Reconciler, "run"): lambda me: None,
            (metrics.Metrics, "record"): lambda me, result: self.recorded.append(result),
        }
        self.saved = {key: getattr(*key) for key in self.patches}
        for (obj, name), value in self.patches.items():
            setattr(obj, name, value)
        os.environ.setdefault("PIHOLE_URL", "http://pihole.invalid")
        FakeClient.error = FakeClient.fail_on = None
        FakeClient.instances = []
        self.recorded = []
        self.sigterm = signal.getsignal(signal.SIGTERM)

    def tearDown(self):
        for (obj, name), value in self.saved.items():
            setattr(obj, name, value)
        signal.signal(signal.SIGTERM, self.sigterm)
        self.dir.cleanup()
        logs.logger.disabled = False

    def main(self):
        """Run main(); return (exit status, the one result handed to the metrics)."""
        status = sync.main()
        self.assertEqual(len(self.recorded), 1, "every run is recorded exactly once")
        return status, self.recorded[0]

    def fail_with(self, where, error):
        FakeClient.fail_on, FakeClient.error = where, error

    def codes(self, result):
        return [(str(e.code), e.fatal) for e in result.errors]

    def test_a_clean_run_exits_0_and_is_a_success(self):
        status, result = self.main()
        self.assertEqual(status, 0)
        self.assertTrue(result.ok)
        self.assertTrue(FakeClient.instances[0].closed, "the session is always closed")

    def test_a_bad_config_exits_0(self):
        pathlib.Path(sync.CLIENTS_PATH).unlink()
        status, result = self.main()
        self.assertEqual((status, self.codes(result)), (0, [("config", True)]))

    def test_pihole_out_of_reach_exits_non_zero(self):
        self.fail_with("login", PiholeError("down", method="GET", route="/api/auth", status=None))
        status, result = self.main()
        self.assertEqual((status, self.codes(result)), (1, [("network", True)]))

    def test_a_refused_password_exits_0(self):
        self.fail_with("login", PiholeError("no", method="POST", route="/api/auth", status=HTTPStatus.UNAUTHORIZED))
        status, result = self.main()
        self.assertEqual((status, self.codes(result)), (0, [("auth", True)]))

    def test_a_failed_gravity_exits_non_zero_and_leaves_the_flag_for_the_retry(self):
        sync.Reconciler.run = lambda me: setattr(me._result, "gravity_required", True)
        self.fail_with("gravity", PiholeError("boom", method="POST", route="/api/action/gravity", status=None))
        status, result = self.main()
        self.assertEqual((status, self.codes(result)), (1, [("gravity", True)]))
        self.assertTrue(os.path.exists(sync._GRAVITY_FLAG))

    def test_the_flag_makes_the_next_run_rebuild_gravity_and_success_clears_it(self):
        os.makedirs(sync.STATE_DIR)
        pathlib.Path(sync._GRAVITY_FLAG).write_text("1")
        status, result = self.main()
        self.assertEqual(status, 0)
        self.assertTrue(result.gravity_required)
        self.assertFalse(os.path.exists(sync._GRAVITY_FLAG))

    def test_anything_unhandled_exits_non_zero_with_this_programs_frames(self):
        def bug(me):
            raise KeyError("groups")

        sync.Reconciler.run = bug
        status, result = self.main()
        self.assertEqual((status, self.codes(result)), (1, [("unknown", True)]))
        frames = result.errors[0].backtrace
        self.assertTrue(frames and all(f.split(":")[0].endswith(".py") for f in frames))
        self.assertTrue(any(f.startswith("sync.py:") for f in frames))
        self.assertFalse(any("test_sync" in f for f in frames), "frames outside the program are dropped")

    def test_sigterm_ends_the_run_reported_and_logged_out(self):
        sync.Reconciler.run = lambda me: os.kill(os.getpid(), signal.SIGTERM)
        status, result = self.main()
        self.assertEqual((status, self.codes(result)), (128 + signal.SIGTERM, [("terminated", True)]))
        self.assertTrue(FakeClient.instances[0].closed)

    def test_what_changed_before_a_fatal_error_is_still_reported(self):
        def partly(me):
            me._result.change(Object.GROUP, Action.ADDED)
            raise PiholeError("down", method="GET", route="/api/clients", status=None)

        sync.Reconciler.run = partly
        status, result = self.main()
        self.assertEqual(status, 1)
        self.assertEqual(sum(result.changes.values()), 1)
        self.assertEqual(self.codes(result), [(str(ErrorCode.NETWORK), True)])
