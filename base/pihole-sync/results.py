"""The outcome of a run: what it changed, and everything that went wrong.

A run produces one `SyncResult`. It is a success when it has no errors and a
failure when it has one or more; there is no in-between, and something that
doesn't matter is a warning in the log, not an error here.

The enums are closed lists, because they are metric labels: `ErrorCode` for
what went wrong, and `Object`, `Action` and `Kind` for what changed. The logs
carry the same values beside the detail. A new value is a change to the
accepted values in docs/current-state/naming-convention.md.
"""

import dataclasses
import enum
from collections import Counter
from http import HTTPStatus

from logs import log_error
from pihole_client import PiholeError


class ErrorCode(enum.StrEnum):
    """The closed list of failure types: the `error` label on pihole_sync_timestamp."""

    CONFIG = "config"  # config or clients file unreadable or invalid
    AUTH = "auth"  # Pi-hole refused the password
    NETWORK = "network"  # Pi-hole unreachable after every retry
    PIHOLE_API = "pihole_api"  # Pi-hole answered, but not with what was needed
    GRAVITY = "gravity"  # the gravity rebuild failed
    TERMINATED = "terminated"  # the pod was stopped mid-run (SIGTERM)
    UNKNOWN = "unknown"  # anything not handled where it happened: a bug, or unforeseen


def classify(exc: PiholeError) -> ErrorCode:
    """The `ErrorCode` a failed call to Pi-hole is reported under.

    It lives here, not in the client, because the codes are this program's
    vocabulary and the client's is HTTP's. Split them further only when one
    status has to mean different things in the two.
    """
    if exc.status is None:
        return ErrorCode.NETWORK
    if exc.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return ErrorCode.AUTH
    return ErrorCode.PIHOLE_API


class Object(enum.StrEnum):
    """What kind of Pi-hole object changed."""

    GROUP = "group"
    CLIENT = "client"
    DOMAIN = "domain"
    LIST = "list"


class Action(enum.StrEnum):
    """What happened to it."""

    ADDED = "added"
    REMOVED = "removed"
    UPDATED = "updated"


class Kind(enum.StrEnum):
    """Allow or block, for domains and lists; the other objects have no kind."""

    ALLOW = "allow"
    BLOCK = "block"


@dataclasses.dataclass(frozen=True)
class Change:
    """One kind of change, such as "a block domain was added"."""

    object: Object
    action: Action
    kind: Kind | None = None

    @property
    def attributes(self) -> dict[str, str]:
        """The metric attributes; `kind` is left out when there is none."""
        attributes = {"object": str(self.object), "action": str(self.action)}
        if self.kind:
            attributes["kind"] = str(self.kind)
        return attributes


# Every kind of change a run can report, so each series can start at zero.
ALL_CHANGES = tuple(
    Change(obj, action, kind)
    for obj, kind in (
        (Object.GROUP, None),
        (Object.CLIENT, None),
        (Object.DOMAIN, Kind.ALLOW),
        (Object.DOMAIN, Kind.BLOCK),
        (Object.LIST, Kind.ALLOW),
        (Object.LIST, Kind.BLOCK),
    )
    for action in Action
)


@dataclasses.dataclass
class Error:
    """One thing that went wrong in a run.

    Attributes:
        code: What kind of failure it was.
        message: What went wrong, for the log.
        fatal: True when it ended the run, False when the run went on.
        exception: The exception's class name, if there was an exception.
        context: Where it happened, such as `method`, `route` and `status`. It
            becomes top-level fields on the log line.
        backtrace: This program's frames, for a failure nothing handled.
    """

    code: ErrorCode
    message: str
    fatal: bool = False
    exception: str | None = None
    context: dict[str, object] = dataclasses.field(default_factory=dict)
    backtrace: list[str] | None = None


@dataclasses.dataclass
class SyncResult:
    """What a run changed, and everything that went wrong.

    The reconciler and the entry point add to the same one as they go, so what
    was changed before a fatal error is still reported.

    Attributes:
        changes: How many objects were changed, by kind of change.
        errors: Every `Error` that came up, fatal or not.
        gravity_required: True when Pi-hole must rebuild its gravity database.
    """

    changes: Counter[Change] = dataclasses.field(default_factory=Counter)
    errors: list[Error] = dataclasses.field(default_factory=list)
    gravity_required: bool = False

    @property
    def ok(self) -> bool:
        """True when the run has no errors."""
        return not self.errors

    def change(self, obj: Object, action: Action, kind: Kind | None = None) -> None:
        """Count one object as changed."""
        self.changes[Change(obj, action, kind)] += 1

    def add_error(self, error: Error, message: str | None = None) -> None:
        """Record an error and log it where it happened.

        Args:
            error: The error.
            message: The log line's message; defaults to the error's own.
        """
        self.errors.append(error)
        log_error(error, message)
