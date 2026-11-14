"""Metrics: pushed over OTLP to the site collector.

The collector translates the names (dots to underscores, the unit and `_total`
as suffixes) and stamps `site`. The metrics say what a run did and how it ended;
Kubernetes already reports when the Job started and finished.

Two blocks live here, because each owns something that must not be forgotten:
`observed_run` owns the end of a run, and `observed_request` owns each HTTP
request to Pi-hole.
"""

import contextlib
import contextvars
import os
import time

from logs import error_field, log, log_error
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from results import RETRIABLE, Error, ErrorCode, Result, classify

# Set to e.g. "zerpzorp." to namespace every metric for a deployment; empty
# by default so the program has no opinion about where it runs.
_METRIC_PREFIX = os.environ.get("METRIC_NAME_PREFIX", "")
_OBJECTS = (
    ("group", None),
    ("client", None),
    ("domain", "allow"),
    ("domain", "block"),
    ("list", "allow"),
    ("list", "block"),
)


def _attrs(obj, action, kind=None):
    """The metric attributes for one kind of change; `kind` is left out when None."""
    attrs = {"object": obj, "action": action}
    if kind:
        attrs["kind"] = kind
    return attrs


class Metrics:
    """The metrics one run reports, over one OpenTelemetry meter provider.

    Attributes:
        provider: The `MeterProvider`. Shutting it down flushes the final push.
        changes: Counter of objects added, removed or updated.
        timestamp: Gauge holding the time of an event, in unix seconds.
        request_duration: Histogram of how long each Pi-hole request took, in
            seconds.
    """

    def __init__(self, readers):
        """Create the instruments, and push a zero for every change series.

        Args:
            readers: The metric readers to export through. Empty means nothing
                is pushed, which is what the tests and a local run use.
        """
        self.provider = MeterProvider(metric_readers=readers, resource=Resource.create({"service.name": "pihole-sync"}))
        meter = self.provider.get_meter("pihole-sync")
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
        # A counter's first sample is its baseline: without a 0 here, the
        # first change a run makes would be invisible to increase().
        for obj, kind in _OBJECTS:
            for action in ("added", "removed", "updated"):
                self.changes.add(0, _attrs(obj, action, kind))

    def record(self, result):
        """Push what a finished run did, and how it ended.

        A failed run has one sample per distinct error code, because a series
        carries one `error` value and a run can end with several kinds.

        Args:
            result: The finished run's `results.Result`. Its changes are added to
                the counter, and its errors decide which `run_finished` samples
                are written.
        """
        for (obj, kind, action), n in result.changes.items():
            self.changes.add(n, _attrs(obj, action, kind))
        now = time.time()
        if result.ok:
            self.timestamp.set(now, {"event": "run_finished", "result": "success"})
        for code in sorted({e.code for e in result.errors}):
            self.timestamp.set(now, {"event": "run_finished", "result": "failure", "error": str(code)})


def push_readers():
    """Build the metric readers for this process, from its environment.

    Returns:
        A list with one periodic OTLP reader when `OTEL_EXPORTER_OTLP_ENDPOINT`
        is set, and an empty list (nothing is pushed) when it isn't.
    """
    # The SDK reads OTEL_EXPORTER_OTLP_ENDPOINT itself; unset means no push.
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return []
    return [PeriodicExportingMetricReader(OTLPMetricExporter())]


# ---------------------------------------------------------------------------
# The blocks that own what must not be forgotten
# ---------------------------------------------------------------------------

# The metrics of the run in progress, so a request can report itself without
# every function between it and main() carrying them.
current_metrics = contextvars.ContextVar("pihole_sync_metrics", default=None)


class Request:
    """What an observed request tells the block that wraps it.

    Attributes:
        status: The HTTP status, set by the body once Pi-hole has answered. Left
            None when the request never got an answer.
    """

    status = None  # set by the body; left None when the request never got an answer


@contextlib.contextmanager
def observed_request(method, path, route):
    """Time one HTTP request and report it however it ends.

        with observed_request("GET", path, route) as request:
            ...
            request.status = status

    It writes one log line with the details (the full path, the status and the
    duration), and one latency sample labelled by `route`, a template of the
    path. Never label by the path itself: it carries domain names and list
    URLs, and each would be a series of its own.

    The histogram is cumulative and each run is a new process, so read it per
    run (`max_over_time(..._count[1h])`), not with rate().

    Args:
        method: The HTTP method, such as "GET". It is a label.
        path: The full path, including any query string. It goes in the log line
            and nowhere else.
        route: The path as a template, such as `/api/groups/{name}`. It is a
            label.

    Yields:
        A `Request`; set its `status` once Pi-hole has answered.

    Raises:
        Whatever the body raises, after the request has been reported with the
        status `error`.
    """
    request = Request()
    started = time.monotonic()
    try:
        yield request
    finally:
        seconds = time.monotonic() - started
        status = "error" if request.status is None else request.status
        log(
            f"{method} {path}",
            warn=status == "error",
            method=method,
            path=path,
            status=status,
            duration_ms=round(seconds * 1000),
        )
        metrics = current_metrics.get()
        if metrics:
            metrics.request_duration.record(seconds, {"method": method, "route": route, "status": str(status)})


class Run:
    """A run in progress.

    Attributes:
        result: The `results.Result` the run adds to as it goes.
        started: When the run began, from `time.monotonic()`.
    """

    def __init__(self):
        self.result = Result()
        self.started = time.monotonic()


@contextlib.contextmanager
def observed_run():
    """Wrap a run so its end is always reported.

        with observed_run() as run:
            do_the_work(run.result)

    However the body ends, an exception becomes one fatal error, then the log
    line, the metrics and the flush happen.

    The exception propagates, and the Job exits non-zero so it is retried, only
    when the error is one a restart can fix (`RETRIABLE`). Any other failure
    ends the block normally and the Job exits 0: whether the run succeeded is
    in the metrics and the logs, not the exit status (ADR 0010).

    Yields:
        A `Run`. The body adds to `run.result`; an exception it raises is turned
        into one fatal error on the result.

    Raises:
        Exception: Only the ones in `results.RETRIABLE`, re-raised after the run
            has been reported. Any other exception is logged and swallowed.
    """
    run = Run()
    metrics = Metrics(push_readers())
    token = current_metrics.set(metrics)
    try:
        yield run
    except Exception as exc:
        code = classify(exc)
        retry = code in RETRIABLE
        error = Error(
            code,
            str(exc),
            fatal=True,
            exception=type(exc).__name__,
            context={"exit_status": "non-zero" if retry else "zero"},
        )
        run.result.errors.append(error)
        log_error(
            error,
            "Sync failed; exiting non-zero so the Job is retried."
            if retry
            else "Sync failed; exiting 0, because a restart can't fix it.",
        )
        if retry:
            raise
    finally:
        current_metrics.reset(token)
        result = run.result
        # The same fact as the metrics, in the log, so each can be found from
        # the other.
        log(
            "Sync finished.",
            event="run_finished",
            result="success" if result.ok else "failure",
            errors=len(result.errors),
            error_codes=sorted({str(e.code) for e in result.errors}),
            duration_s=round(time.monotonic() - run.started),
        )
        try:
            metrics.record(result)
            # Flushes the final push; a collector outage must not fail a sync.
            metrics.provider.shutdown()
        except Exception as exc:  # noqa: BLE001 - telemetry must never fail a sync
            log(
                "Metrics push failed.",
                warn=True,
                **error_field(ErrorCode.UNKNOWN, str(exc), exception=type(exc).__name__),
            )
