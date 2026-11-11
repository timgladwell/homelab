"""base/pihole-sync/observability.py — what the program reports about itself.

Metrics carry only fixed values (the ErrorCode enum); the detail goes to the
log. Metric names are asserted in OTLP form, as the collector translates them
to pihole_sync_changes_total and pihole_sync_timestamp_seconds.
"""
import unittest

from loader import load
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

# sync.py reads PIHOLE_URL at import; loading it also puts its directory on
# the path, which is how observability is found in the pod too.
load("base/pihole-sync/sync.py", env={"PIHOLE_URL": "http://pihole.invalid"})
import observability as obs  # noqa: E402


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
        self.real = obs.metrics
        self.reader = InMemoryMetricReader()
        obs.metrics = obs.Metrics([self.reader])

    def tearDown(self):
        obs.metrics = self.real

    def test_every_series_starts_at_zero(self):
        # increase() needs a baseline; a first sample of 1 reads as no change.
        changes = metric_points(self.reader, "pihole_sync.changes")
        self.assertEqual(len(changes), 6 * 3)
        self.assertEqual(set(changes.values()), {0})

    def test_finished_stamps_the_event_with_its_result_and_error(self):
        obs.metrics.finished("partial", obs.ErrorCode.PIHOLE_API)
        obs.metrics.finished("success", None)
        stamps = metric_points(self.reader, "pihole_sync.timestamp")
        keys = {frozenset({"event": "run_finished", "result": "partial",
                           "error": "pihole_api"}.items()),
                frozenset({"event": "run_finished", "result": "success"}.items())}
        self.assertEqual(set(stamps), keys)
        # The value is the event's own time, in unix seconds.
        self.assertTrue(all(v > 1_700_000_000 for v in stamps.values()))


class Errors(unittest.TestCase):
    def test_every_code_is_a_valid_label_value(self):
        self.assertEqual(
            {str(c) for c in obs.ErrorCode},
            {"config", "auth", "network", "pihole_api", "gravity", "unknown"})

    def test_classify(self):
        self.assertEqual(obs.classify(obs.SyncError(obs.ErrorCode.AUTH, "x")), "auth")
        self.assertEqual(obs.classify(OSError("refused")), "network")
        self.assertEqual(obs.classify(KeyError("groups")), "unknown")


class Logfmt(unittest.TestCase):
    def render(self, **fields):
        record = obs.logger.makeRecord(
            "pihole-sync", 40, __file__, 0, "a message", (), None,
            extra={"fields": fields})
        return obs.Logfmt().format(record)

    def test_fields_follow_the_message(self):
        line = self.render(code=obs.ErrorCode.AUTH, status=401)
        self.assertRegex(line, r'^time=\S+ level=error msg="a message" code=auth status=401$')

    def test_a_value_with_spaces_or_quotes_is_quoted_so_it_stays_one_field(self):
        self.assertIn('detail="HTTP 500: {\\"error\\": \\"x\\"}"',
                      self.render(detail='HTTP 500: {"error": "x"}'))


if __name__ == "__main__":
    unittest.main()
