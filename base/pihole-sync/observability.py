"""What pihole-sync reports about itself: errors, logs and metrics.

A run produces one Result: what it changed, and every error that came up. The
metrics carry the overall story with fixed values (ErrorCode), and the logs
carry the detail. observed_run() is the one block that owns the rest, so a run
can't forget to log how it ended or push its metrics.

Nothing here knows about Pi-hole, so it can leave with the program if that
becomes its own image.
"""

import contextlib
import dataclasses
import enum
import json
import logging
import os
import sys
import time
from collections import Counter

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

# ---------------------------------------------------------------------------
# Errors and the Result of a run
# ---------------------------------------------------------------------------


class ErrorCode(enum.StrEnum):
    """The closed list of failure types.

    Metrics carry only these (the `error` label on pihole_sync_timestamp), and
    logs carry the same value as `error.code` beside the detail. A new value is
    a change to the accepted values in docs/current-state/naming-convention.md.
    """

    CONFIG = "config"  # config or clients file unreadable or invalid
    AUTH = "auth"  # Pi-hole refused the password or gave no session
    NETWORK = "network"  # Pi-hole unreachable after every retry
    PIHOLE_API = "pihole_api"  # Pi-hole answered, but not with what was needed
    GRAVITY = "gravity"  # the gravity rebuild failed
    UNKNOWN = "unknown"  # anything not classified above: a bug


class SyncError(Exception):
    """Raised when the run can't go on: it ends there, and is one fatal Error."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def classify(exc):
    if isinstance(exc, SyncError):
        return exc.code
    if isinstance(exc, OSError):  # URLError and socket timeouts
        return ErrorCode.NETWORK
    return ErrorCode.UNKNOWN


@dataclasses.dataclass
class Error:
    code: ErrorCode
    message: str
    fatal: bool = False
    exception: str | None = None  # the exception class name, if there was one
    context: dict = dataclasses.field(default_factory=dict)  # method, path, status...


@dataclasses.dataclass
class Result:
    """The outcome of the processing: what changed, and everything that went wrong.

    A run is a success when it has no errors, and a failure when it has one or
    more. There is no in-between: something that doesn't matter is a warning in
    the log, not an error here.
    """

    changes: Counter = dataclasses.field(default_factory=Counter)  # (object, kind, action) -> n
    errors: list = dataclasses.field(default_factory=list)
    gravity_needed: bool = False

    @property
    def ok(self):
        return not self.errors

    def change(self, obj, action, kind=None):
        self.changes[(obj, kind, action)] += 1

    def fail(self, code, message, **context):
        """Record an error and log it where it happened."""
        error = Error(code, message, context=context)
        self.errors.append(error)
        log_error(error)

    def merge(self, other):
        self.changes.update(other.changes)
        self.errors.extend(other.errors)
        self.gravity_needed |= other.gravity_needed
        return self


# ---------------------------------------------------------------------------
# Logging: one JSON object per line, so a line is parsed and filtered by field
# (`| json` in Loki) rather than matched by text. Metrics say what happened; the
# detail, errors especially, is here.
# ---------------------------------------------------------------------------


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": _ts(),
                "level": record.levelname.lower(),
                "msg": record.getMessage(),
                **getattr(record, "fields", {}),
            },
            default=str,
        )


logger = logging.getLogger("pihole-sync")
logger.setLevel(logging.INFO)
logger.propagate = False
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logger.addHandler(_handler)


def log(msg, *, err=False, warn=False, **fields):
    level = logging.ERROR if err else logging.WARNING if warn else logging.INFO
    logger.log(level, msg.strip(), extra={"fields": fields})


def error_field(code, message, *, exception=None, fatal=False, retry=None):
    """The `error` object every error and warning line carries.

    `retry` ({"iteration", "max_iterations"}) is present only when the action
    will be tried again.
    """
    error = {"code": str(code), "fatal": fatal, "message": message}
    if exception:
        error["exception"] = exception
    if retry:
        error["retry"] = retry
    return {"error": error}


def log_error(error, msg=None):
    fields = dict(error.context)
    fields.update(error_field(error.code, error.message, exception=error.exception, fatal=error.fatal))
    log(msg or error.message, err=True, **fields)


# ---------------------------------------------------------------------------
# Metrics: pushed over OTLP to the site collector, which translates the names
# (dots to underscores, the unit and _total as suffixes) and stamps `site`.
# They say what a run did and how it ended; Kubernetes already reports when
# the Job started and finished.
# ---------------------------------------------------------------------------

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
    attrs = {"object": obj, "action": action}
    if kind:
        attrs["kind"] = kind
    return attrs


class Metrics:
    def __init__(self, readers):
        self.provider = MeterProvider(metric_readers=readers, resource=Resource.create({"service.name": "pihole-sync"}))
        meter = self.provider.get_meter("pihole-sync")
        self.changes = meter.create_counter(
            f"{_METRIC_PREFIX}pihole_sync.changes", description="Objects this run added, removed or updated in Pi-hole"
        )
        self.timestamp = meter.create_gauge(
            f"{_METRIC_PREFIX}pihole_sync.timestamp", unit="s", description="Time of a sync event, in unix seconds"
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
        """
        for (obj, kind, action), n in result.changes.items():
            self.changes.add(n, _attrs(obj, action, kind))
        now = time.time()
        if result.ok:
            self.timestamp.set(now, {"event": "run_finished", "result": "success"})
        for code in sorted({e.code for e in result.errors}):
            self.timestamp.set(now, {"event": "run_finished", "result": "failure", "error": str(code)})


def push_readers():
    # The SDK reads OTEL_EXPORTER_OTLP_ENDPOINT itself; unset means no push.
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return []
    return [PeriodicExportingMetricReader(OTLPMetricExporter())]


# ---------------------------------------------------------------------------
# The block that owns the end of a run
# ---------------------------------------------------------------------------


class Run:
    def __init__(self):
        self.result = Result()
        self.started = time.monotonic()


@contextlib.contextmanager
def observed_run():
    """Wrap a run so its end is always reported.

        with observed_run() as run:
            do_the_work(run.result)

    However the body ends, an exception becomes one fatal error, then the log
    line, the metrics and the flush happen. The exception still propagates, so
    the Job's exit status is the program's own.
    """
    run = Run()
    metrics = Metrics(push_readers())
    try:
        yield run
    except Exception as exc:
        error = Error(classify(exc), str(exc), fatal=True, exception=type(exc).__name__)
        run.result.errors.append(error)
        log_error(error, "Sync failed.")
        raise
    finally:
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
