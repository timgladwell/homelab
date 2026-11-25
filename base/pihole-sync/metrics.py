"""Metrics: pushed over OTLP to the site collector.

The collector translates the names (dots to underscores, the unit and `_total`
as suffixes) and stamps `site`. The metrics say what a run did and how it ended;
Kubernetes already reports when the Job started and finished.

The entry point creates one `Metrics` and hands it to whatever reports through
it. The final push happens at exit: the SDK's `MeterProvider` registers its own
shutdown with `atexit`, and shutting down flushes.
"""

import os
import time
from collections.abc import Iterable, Mapping
from typing import Self

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

# Set to e.g. "zerpzorp." to namespace every metric for a deployment; empty
# by default so the program has no opinion about where it runs.
_METRIC_PREFIX = os.environ.get("METRIC_NAME_PREFIX", "")


class Metrics:
    """The metrics one run reports, over one OpenTelemetry meter provider.

    Attributes:
        changes: Counter of objects added, removed or updated.
        timestamp: Gauge holding the time of an event, in unix seconds.
        request_duration: Histogram of how long each Pi-hole request took, in
            seconds.
    """

    def __init__(self, readers: list[MetricReader], baseline: Iterable[Mapping[str, str]] = ()):
        """Create the instruments, and push a zero for every change series.

        Args:
            readers: The metric readers to export through. Empty means nothing
                is pushed, which is what a local run uses; the tests pass an
                `InMemoryMetricReader`.
            baseline: The attributes of every `changes` series, each pushed at
                0 first. Each run is a new process, and without a baseline the
                first change a run makes would be invisible to increase().
        """
        provider = MeterProvider(metric_readers=readers, resource=Resource.create({"service.name": "pihole-sync"}))
        meter = provider.get_meter("pihole-sync")
        self.changes = meter.create_counter(
            f"{_METRIC_PREFIX}pihole_sync.changes", description="Objects this run added, removed or updated in Pi-hole"
        )
        self.timestamp = meter.create_gauge(
            f"{_METRIC_PREFIX}pihole_sync.timestamp", unit="s", description="Time of a sync event, in unix seconds"
        )
        # The SDK's default buckets are sized for milliseconds.
        self.request_duration = meter.create_histogram(
            f"{_METRIC_PREFIX}pihole_sync.request.duration",
            unit="s",
            description="How long each HTTP request to Pi-hole took",
            explicit_bucket_boundaries_advisory=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60],
        )
        for attributes in baseline:
            self.changes.add(0, attributes)

    @classmethod
    def from_environment(cls, baseline: Iterable[Mapping[str, str]] = ()) -> Self:
        """The metrics for this process: pushed over OTLP when the environment says where.

        The SDK reads `OTEL_EXPORTER_OTLP_ENDPOINT` itself; unset, nothing is
        pushed.

        Args:
            baseline: As for the constructor.
        """
        readers = (
            [PeriodicExportingMetricReader(OTLPMetricExporter())]
            if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            else []
        )
        return cls(readers, baseline)

    def record_request(self, method: str, route: str, status: str, seconds: float) -> None:
        """Record how long one request to Pi-hole took.

        The histogram is cumulative and each run is a new process, so read it
        per run (`max_over_time(..._count[1h])`), not with rate().

        Args:
            method: The HTTP method, such as "GET".
            route: The path template, such as `/api/groups/{name}`. Never the
                path itself: it carries domain names and list URLs, and each
                would be a series of its own.
            status: The HTTP status as text, or "error" when there was no answer.
            seconds: How long the attempt took.
        """
        self.request_duration.record(seconds, {"method": method, "route": route, "status": status})

    def record(self, result) -> None:
        """Record what a finished run did, and how it ended.

        A failed run has one sample per distinct error code, because a series
        carries one `error` value and a run can end with several kinds.

        Args:
            result: The finished run's `results.SyncResult`.
        """
        for change, n in result.changes.items():
            self.changes.add(n, change.attributes)
        now = time.time()
        if result.ok:
            self.timestamp.set(now, {"event": "run_finished", "result": "success"})
        for code in sorted({e.code for e in result.errors}):
            self.timestamp.set(now, {"event": "run_finished", "result": "failure", "error": str(code)})
