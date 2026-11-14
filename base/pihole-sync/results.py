"""The outcome of a run: what it changed, and everything that went wrong.

A run produces one `Result`. It is a success when it has no errors and a failure
when it has one or more; there is no in-between, and something that doesn't
matter is a warning in the log, not an error here. The sync functions each
return a `Result`, and the run merges them.

`ErrorCode` is the closed list of failure types. It is what the metrics label
carries, and the logs carry the same value beside the detail.
"""

import dataclasses
import enum
from collections import Counter

from logs import log_error


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
