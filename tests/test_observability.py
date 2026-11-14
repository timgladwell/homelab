"""base/pihole-sync/observability.py — what the program reports about itself.

Metrics carry only fixed values (the ErrorCode enum); the detail goes to the
log. Metric names are asserted in OTLP form, as the collector translates them
to pihole_sync_changes_total and pihole_sync_timestamp_seconds.
"""

import json
import unittest

from loader import load
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

# sync.py reads PIHOLE_URL at import; loading it also puts its directory on
# the path, which is how observability is found in the pod too.
load("base/pihole-sync/sync.py", env={"PIHOLE_URL": "http://pihole.invalid"})
import observability as obs


def metric_points(reader, name):
    """{frozenset(attributes.items()): value} for one metric."""
    found = {}
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    for dp in metric.data.data_points:
                        found[frozenset(dp.attributes.items())] = dp.value
    return found


class Metrics(unittest.TestCase):
    def setUp(self):
        self.reader = InMemoryMetricReader()
        self.metrics = obs.Metrics([self.reader])
        obs.logger.disabled = True  # Result.fail logs the error it records

    def tearDown(self):
        obs.logger.disabled = False

    def stamps(self):
        return set(metric_points(self.reader, "pihole_sync.timestamp"))

    def test_every_series_starts_at_zero(self):
        # increase() needs a baseline; a first sample of 1 reads as no change.
        changes = metric_points(self.reader, "pihole_sync.changes")
        self.assertEqual(len(changes), 6 * 3)
        self.assertEqual(set(changes.values()), {0})

    def test_a_result_adds_its_changes_by_object_kind_and_action(self):
        result = obs.Result()
        result.change("domain", "added", "block")
        result.change("domain", "added", "block")
        result.change("group", "removed")
        self.metrics.record(result)
        changes = metric_points(self.reader, "pihole_sync.changes")
        self.assertEqual(changes[frozenset({"object": "domain", "kind": "block", "action": "added"}.items())], 2)
        self.assertEqual(changes[frozenset({"object": "group", "action": "removed"}.items())], 1)

    def test_a_run_without_errors_is_one_success_sample(self):
        self.metrics.record(obs.Result())
        self.assertEqual(self.stamps(), {frozenset({"event": "run_finished", "result": "success"}.items())})

    def test_a_failed_run_has_one_sample_per_distinct_error_code(self):
        result = obs.Result()
        result.fail(obs.ErrorCode.PIHOLE_API, "one")
        result.fail(obs.ErrorCode.PIHOLE_API, "two")
        result.fail(obs.ErrorCode.GRAVITY, "three")
        self.metrics.record(result)
        self.assertEqual(
            self.stamps(),
            {
                frozenset({"event": "run_finished", "result": "failure", "error": "pihole_api"}.items()),
                frozenset({"event": "run_finished", "result": "failure", "error": "gravity"}.items()),
            },
        )

    def test_the_value_is_the_events_own_time_in_unix_seconds(self):
        self.metrics.record(obs.Result())
        (value,) = metric_points(self.reader, "pihole_sync.timestamp").values()
        self.assertGreater(value, 1_700_000_000)


class Errors(unittest.TestCase):
    def test_every_code_is_a_valid_label_value(self):
        self.assertEqual(
            {str(c) for c in obs.ErrorCode}, {"config", "auth", "network", "pihole_api", "gravity", "unknown"}
        )

    def test_classify(self):
        self.assertEqual(obs.classify(obs.SyncError(obs.ErrorCode.AUTH, "x")), "auth")
        self.assertEqual(obs.classify(OSError("refused")), "network")
        self.assertEqual(obs.classify(KeyError("groups")), "unknown")

    def test_a_result_with_no_errors_is_ok_and_any_error_makes_it_a_failure(self):
        result = obs.Result()
        self.assertTrue(result.ok)
        obs.logger.disabled = True
        try:
            result.fail(obs.ErrorCode.PIHOLE_API, "boom")
        finally:
            obs.logger.disabled = False
        self.assertFalse(result.ok)


class JsonLogs(unittest.TestCase):
    def render(self, msg="a message", level=40, **fields):
        record = obs.logger.makeRecord("pihole-sync", level, __file__, 0, msg, (), None, extra={"fields": fields})
        return json.loads(obs.JsonFormatter().format(record))

    def test_a_line_is_one_json_object_with_time_level_and_msg(self):
        line = self.render(status=401)
        self.assertEqual((line["level"], line["msg"], line["status"]), ("error", "a message", 401))
        self.assertRegex(line["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_an_error_object_carries_the_code_the_exception_and_the_message(self):
        line = self.render(**obs.error_field(obs.ErrorCode.NETWORK, "connection refused", exception="URLError"))
        self.assertEqual(
            line["error"], {"code": "network", "fatal": False, "message": "connection refused", "exception": "URLError"}
        )

    def test_retry_is_present_only_when_the_action_will_be_retried(self):
        retry = {"iteration": 1, "max_iterations": 6}
        self.assertEqual(obs.error_field("network", "x", retry=retry)["error"]["retry"], retry)
        self.assertNotIn("retry", obs.error_field("network", "x")["error"])

    def test_a_value_that_is_not_json_is_still_written(self):
        # A line that can't be written is a line that can't explain a failure.
        self.assertEqual(self.render(thing=object())["thing"][:8], "<object ")


if __name__ == "__main__":
    unittest.main()
