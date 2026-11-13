"""What pihole-sync reports about itself: errors, logs and metrics.

Metrics carry the overall story with fixed values (ErrorCode); logs carry the
detail. Nothing here knows about Pi-hole, so it can leave with the program if
that becomes its own image.
"""
import enum
import json
import logging
import os
import re
import sys
import time

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ErrorCode(enum.StrEnum):
    """The closed list of failure types.

    Metrics carry only these (the `error` label on pihole_sync_timestamp), and
    logs carry the same value as `code=` beside the detail. A new value is a
    change to the accepted values in docs/current-state/naming-convention.md.
    """
    CONFIG = "config"          # config or clients file unreadable or invalid
    AUTH = "auth"              # Pi-hole refused the password or gave no session
    NETWORK = "network"        # Pi-hole unreachable after every retry
    PIHOLE_API = "pihole_api"  # Pi-hole answered, but not with what was needed
    GRAVITY = "gravity"        # the gravity rebuild failed
    UNKNOWN = "unknown"        # anything not classified above: a bug


class SyncError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def classify(exc):
    if isinstance(exc, SyncError):
        return exc.code
    if isinstance(exc, OSError):  # URLError and socket timeouts
        return ErrorCode.NETWORK
    return ErrorCode.UNKNOWN


# Per-object failures are logged and the run carries on, so one bad domain
# doesn't stop the rest. They are remembered here to make the run `partial`.
soft_errors = []


def soft_error(detail):
    soft_errors.append(detail)
    log(detail, err=True, code=ErrorCode.PIHOLE_API)


# ---------------------------------------------------------------------------
# Logging: one logfmt line per record. Metrics say what happened; the detail,
# errors especially, is here.
# ---------------------------------------------------------------------------

_SIMPLE = re.compile(r"[\w.:/@+-]+")


def _quote(value):
    value = str(value)
    return value if _SIMPLE.fullmatch(value) else json.dumps(value)


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Logfmt(logging.Formatter):
    def format(self, record):
        fields = {"level": record.levelname.lower(), "msg": record.getMessage(),
                  **getattr(record, "fields", {})}
        return " ".join([f"time={_ts()}"] + [f"{k}={_quote(v)}" for k, v in fields.items()])


logger = logging.getLogger("pihole-sync")
logger.setLevel(logging.INFO)
logger.propagate = False
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(Logfmt())
logger.addHandler(_handler)


def log(msg, *, err=False, warn=False, **fields):
    level = logging.ERROR if err else logging.WARNING if warn else logging.INFO
    logger.log(level, msg.strip(), extra={"fields": fields})


# ---------------------------------------------------------------------------
# Metrics: pushed over OTLP to the site collector, which translates the names
# (dots to underscores, the unit and _total as suffixes) and stamps `site`.
# They say what a run did and how it ended; Kubernetes already reports when
# the Job started and finished.
# ---------------------------------------------------------------------------

# Set to e.g. "zerpzorp." to namespace every metric for a deployment; empty
# by default so the program has no opinion about where it runs.
_METRIC_PREFIX = os.environ.get("METRIC_NAME_PREFIX", "")
_OBJECTS = (("group", None), ("client", None),
            ("domain", "allow"), ("domain", "block"),
            ("list", "allow"), ("list", "block"))


def _attrs(obj, action, kind=None):
    attrs = {"object": obj, "action": action}
    if kind:
        attrs["kind"] = kind
    return attrs


class Metrics:
    def __init__(self, readers):
        self.provider = MeterProvider(
            metric_readers=readers,
            resource=Resource.create({"service.name": "pihole-sync"}))
        meter = self.provider.get_meter("pihole-sync")
        self.changes = meter.create_counter(
            f"{_METRIC_PREFIX}pihole_sync.changes",
            description="Objects this run added, removed or updated in Pi-hole")
        self.timestamp = meter.create_gauge(
            f"{_METRIC_PREFIX}pihole_sync.timestamp", unit="s",
            description="Time of a sync event, in unix seconds")
        # A counter's first sample is its baseline: without a 0 here, the
        # first change a run makes would be invisible to increase().
        for obj, kind in _OBJECTS:
            for action in ("added", "removed", "updated"):
                self.changes.add(0, _attrs(obj, action, kind))

    def finished(self, result, error):
        attrs = {"event": "run_finished", "result": result}
        if error:
            attrs["error"] = str(error)
        self.timestamp.set(time.time(), attrs)


def push_readers():
    # The SDK reads OTEL_EXPORTER_OTLP_ENDPOINT itself; unset means no push.
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return []
    return [PeriodicExportingMetricReader(OTLPMetricExporter())]


metrics = Metrics([])


def changed(obj, action, kind=None):
    metrics.changes.add(1, _attrs(obj, action, kind))
