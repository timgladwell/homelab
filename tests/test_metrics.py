"""base/pihole-sync/metrics.py — what the program reports about itself.

Metrics carry only fixed values (the enums in results.py); the detail goes to
the log. Metric names are asserted in OTLP form, as the collector translates
them to pihole_sync_changes_total and pihole_sync_timestamp_seconds.
"""

import unittest

import logs
import metrics
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from results import ALL_CHANGES, Action, Error, ErrorCode, Kind, Object, SyncResult


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


def failed(*codes):
    """A result with one error per code."""
    result = SyncResult()
    logs.logger.disabled = True  # add_error logs the error it records
    try:
        for code in codes:
            result.add_error(Error(code, "boom"))
    finally:
        logs.logger.disabled = False
    return result


class Metrics(unittest.TestCase):
    def setUp(self):
        self.reader = InMemoryMetricReader()
        self.metrics = metrics.Metrics([self.reader], baseline=[c.attributes for c in ALL_CHANGES])

    def stamps(self):
        return set(metric_points(self.reader, "pihole_sync.timestamp"))

    def test_every_series_starts_at_zero(self):
        # increase() needs a baseline; a first sample of 1 reads as no change.
        changes = metric_points(self.reader, "pihole_sync.changes")
        self.assertEqual(len(changes), 6 * 3)
        self.assertEqual(set(changes.values()), {0})

    def test_a_result_adds_its_changes_by_object_kind_and_action(self):
        result = SyncResult()
        result.change(Object.DOMAIN, Action.ADDED, Kind.BLOCK)
        result.change(Object.DOMAIN, Action.ADDED, Kind.BLOCK)
        result.change(Object.GROUP, Action.REMOVED)
        self.metrics.record(result)
        changes = metric_points(self.reader, "pihole_sync.changes")
        self.assertEqual(changes[frozenset({"object": "domain", "kind": "block", "action": "added"}.items())], 2)
        self.assertEqual(changes[frozenset({"object": "group", "action": "removed"}.items())], 1)

    def test_a_run_without_errors_is_one_success_sample(self):
        self.metrics.record(SyncResult())
        self.assertEqual(self.stamps(), {frozenset({"event": "run_finished", "result": "success"}.items())})

    def test_a_failed_run_has_one_sample_per_distinct_error_code(self):
        self.metrics.record(failed(ErrorCode.PIHOLE_API, ErrorCode.PIHOLE_API, ErrorCode.GRAVITY))
        self.assertEqual(
            self.stamps(),
            {
                frozenset({"event": "run_finished", "result": "failure", "error": "pihole_api"}.items()),
                frozenset({"event": "run_finished", "result": "failure", "error": "gravity"}.items()),
            },
        )

    def test_the_value_is_the_events_own_time_in_unix_seconds(self):
        self.metrics.record(SyncResult())
        (value,) = metric_points(self.reader, "pihole_sync.timestamp").values()
        self.assertGreater(value, 1_700_000_000)

    def test_a_request_is_labelled_by_its_route(self):
        self.metrics.record_request("GET", "/api/groups/{name}", "200", 0.01)
        self.assertEqual(
            metric_points(self.reader, "pihole_sync.request.duration"),
            {frozenset({"method": "GET", "route": "/api/groups/{name}", "status": "200"}.items()): 1},
        )
