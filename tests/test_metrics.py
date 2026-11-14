"""base/pihole-sync/metrics.py — what the program reports about itself.

Metrics carry only fixed values (the ErrorCode enum); the detail goes to the
log. Metric names are asserted in OTLP form, as the collector translates them
to pihole_sync_changes_total and pihole_sync_timestamp_seconds.
"""

import unittest

import logs
import metrics
import results
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


def metric_points(reader, name):
    """{frozenset(attributes.items()): value} for one metric."""
    found = {}
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    for dp in metric.data.data_points:
                        # A histogram's point has a count, not a value.
                        found[frozenset(dp.attributes.items())] = dp.value if hasattr(dp, "value") else dp.count
    return found


class Metrics(unittest.TestCase):
    def setUp(self):
        self.reader = InMemoryMetricReader()
        self.metrics = metrics.Metrics([self.reader])
        logs.logger.disabled = True  # Result.fail logs the error it records

    def tearDown(self):
        logs.logger.disabled = False

    def stamps(self):
        return set(metric_points(self.reader, "pihole_sync.timestamp"))

    def test_every_series_starts_at_zero(self):
        # increase() needs a baseline; a first sample of 1 reads as no change.
        changes = metric_points(self.reader, "pihole_sync.changes")
        self.assertEqual(len(changes), 6 * 3)
        self.assertEqual(set(changes.values()), {0})

    def test_a_result_adds_its_changes_by_object_kind_and_action(self):
        result = results.Result()
        result.change("domain", "added", "block")
        result.change("domain", "added", "block")
        result.change("group", "removed")
        self.metrics.record(result)
        changes = metric_points(self.reader, "pihole_sync.changes")
        self.assertEqual(changes[frozenset({"object": "domain", "kind": "block", "action": "added"}.items())], 2)
        self.assertEqual(changes[frozenset({"object": "group", "action": "removed"}.items())], 1)

    def test_a_run_without_errors_is_one_success_sample(self):
        self.metrics.record(results.Result())
        self.assertEqual(self.stamps(), {frozenset({"event": "run_finished", "result": "success"}.items())})

    def test_a_failed_run_has_one_sample_per_distinct_error_code(self):
        result = results.Result()
        result.fail(results.ErrorCode.PIHOLE_API, "one")
        result.fail(results.ErrorCode.PIHOLE_API, "two")
        result.fail(results.ErrorCode.GRAVITY, "three")
        self.metrics.record(result)
        self.assertEqual(
            self.stamps(),
            {
                frozenset({"event": "run_finished", "result": "failure", "error": "pihole_api"}.items()),
                frozenset({"event": "run_finished", "result": "failure", "error": "gravity"}.items()),
            },
        )

    def test_the_value_is_the_events_own_time_in_unix_seconds(self):
        self.metrics.record(results.Result())
        (value,) = metric_points(self.reader, "pihole_sync.timestamp").values()
        self.assertGreater(value, 1_700_000_000)


class ObservedRequest(unittest.TestCase):
    def setUp(self):
        self.reader = InMemoryMetricReader()
        self.token = metrics.current_metrics.set(metrics.Metrics([self.reader]))

    def tearDown(self):
        metrics.current_metrics.reset(self.token)

    def samples(self):
        return metric_points(self.reader, "pihole_sync.request.duration")

    def test_a_request_is_one_log_line_with_its_details_and_one_latency_sample(self):
        with (
            self.assertLogs("pihole-sync", level="INFO") as captured,
            metrics.observed_request("GET", "/api/groups/Kids", "/api/groups/{name}") as request,
        ):
            request.status = 200
        (record,) = captured.records
        self.assertEqual(
            {k: record.fields[k] for k in ("method", "path", "status")},
            {"method": "GET", "path": "/api/groups/Kids", "status": 200},
        )
        self.assertIn("duration_ms", record.fields)
        # The series is labelled by the route, never the path.
        self.assertEqual(
            self.samples(), {frozenset({"method": "GET", "route": "/api/groups/{name}", "status": "200"}.items()): 1}
        )

    def test_a_request_that_gets_no_answer_is_reported_as_an_error_and_still_raises(self):
        with (
            self.assertLogs("pihole-sync", level="INFO") as captured,
            self.assertRaises(OSError),
            metrics.observed_request("POST", "/api/auth", "/api/auth"),
        ):
            raise OSError("connection refused")
        self.assertEqual((captured.records[0].levelname, captured.records[0].fields["status"]), ("WARNING", "error"))
        self.assertEqual(
            set(self.samples()), {frozenset({"method": "POST", "route": "/api/auth", "status": "error"}.items())}
        )

    def test_outside_a_run_it_logs_and_records_nothing(self):
        metrics.current_metrics.set(None)  # no run in progress; tearDown restores
        with (
            self.assertLogs("pihole-sync", level="INFO"),
            metrics.observed_request("GET", "/api/auth", "/api/auth") as request,
        ):
            request.status = 200
        self.assertEqual(self.samples(), {})
