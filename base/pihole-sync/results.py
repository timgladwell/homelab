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


# The failures a prompt restart can fix, each with why. A run that ends on any
# other code exits 0: the Job is not retried, because the outcome is in the
# metrics and the logs, not in the exit status (docs/adr/0010-how-a-job-reports-
# its-outcome.md). Decide a new ErrorCode here when it is added, not later.
#
# Not retriable: CONFIG (the ConfigMap is wrong, and a restart reads the same
# one), AUTH (the password doesn't change when the Job does), UNKNOWN (a bug).
RETRIABLE = {
    ErrorCode.NETWORK: "Pi-hole may be restarting or briefly unreachable",
    ErrorCode.PIHOLE_API: "Pi-hole answered with an error, often while it restarts or rebuilds",
    ErrorCode.GRAVITY: "the gravity flag file makes the next attempt re-run the rebuild",
}


class SyncError(Exception):
    """Raised when the run can't go on: it ends there, and is one fatal Error.

    Attributes:
        code: The `ErrorCode` that says what kind of failure it was.
    """

    def __init__(self, code, message):
        """Create the error.

        Args:
            code: The `ErrorCode` for this failure.
            message: What went wrong, for the log.
        """
        super().__init__(message)
        self.code = code


def classify(exc):
    """Say which `ErrorCode` an exception stands for.

    Args:
        exc: Any exception that ended a run.

    Returns:
        The exception's own code if it is a `SyncError`, `NETWORK` for an
        `OSError` (which covers `URLError` and socket timeouts), and `UNKNOWN`
        for anything else, which is a bug.
    """
    if isinstance(exc, SyncError):
        return exc.code
    if isinstance(exc, OSError):  # URLError and socket timeouts
        return ErrorCode.NETWORK
    return ErrorCode.UNKNOWN


@dataclasses.dataclass
class Error:
    """One thing that went wrong in a run.

    Attributes:
        code: What kind of failure it was.
        message: What went wrong, for the log.
        fatal: True when it ended the run, False when the run went on.
        exception: The exception's class name, if there was an exception.
        context: Where it happened, such as `method`, `path` and `status`. It
            becomes top-level fields on the log line.
    """

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

    Attributes:
        changes: How many objects were changed, keyed by
            `(object, kind, action)`, such as `("domain", "block", "added")`.
        errors: Every `Error` that came up, fatal or not.
        gravity_needed: True when what changed means Pi-hole must rebuild its
            gravity database.
    """

    changes: Counter = dataclasses.field(default_factory=Counter)  # (object, kind, action) -> n
    errors: list = dataclasses.field(default_factory=list)
    gravity_needed: bool = False

    @property
    def ok(self):
        """True when the run has no errors."""
        return not self.errors

    def change(self, obj, action, kind=None):
        """Count one object as changed.

        Args:
            obj: What kind of object: "group", "client", "domain" or "list".
            action: "added", "removed" or "updated".
            kind: "allow" or "block" for a domain or a list, and None for the
                objects that have no such split.
        """
        self.changes[(obj, kind, action)] += 1

    def fail(self, code, message, **context):
        """Record a non-fatal error and log it where it happened.

        Args:
            code: The `ErrorCode` for this failure.
            message: What went wrong.
            **context: Where it happened, such as `method`, `path` and `status`.
        """
        error = Error(code, message, context=context)
        self.errors.append(error)
        log_error(error)

    def merge(self, other):
        """Add another result's changes and errors to this one.

        Args:
            other: The `Result` of one step, such as syncing the groups.

        Returns:
            This result, so a call can be chained.
        """
        self.changes.update(other.changes)
        self.errors.extend(other.errors)
        self.gravity_needed |= other.gravity_needed
        return self
