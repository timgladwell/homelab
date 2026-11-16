#!/usr/bin/env python3
"""Pi-hole v6 configuration sync: the entry point, and the lifecycle of one run.

A run loads the config, logs in to Pi-hole, reconciles it against the config,
rebuilds gravity if that needs it, and reports how it went. This file owns the
decisions about the run itself; the rest is split by responsibility, each module
with its own docstring:
  pihole_client  Pi-hole's API as Python objects: requests, retries, the session
  reconcile      making Pi-hole's groups, lists, domains and clients match the config
  results        the outcome of a run, and the closed lists its metrics carry
  logs           the JSON log lines
  metrics        the metrics, pushed over OTLP

The exit status only ever means "retry me" (docs/adr/0010-how-a-job-reports-
its-outcome.md); whether the run succeeded is in the run_finished metric and
log line. Each failure this program expects is handled where it is decided,
with its own exit status. Anything else reaches the catch-all in `main`, which
exits non-zero: an unforeseen failure is retried rather than given up on.

Gravity is rebuilt when:
  - any adlist is added or removed in this run, OR
  - any desired adlist has 0 domains loaded (a prior rebuild didn't complete
    for it), OR
  - the gravity flag file exists (set by a previous attempt in this pod before
    its rebuild completed, so a retry always re-attempts it).
"""

import os
import signal
import sys
import time
from typing import Any

import yaml
from logs import backtrace, log
from metrics import Metrics
from pihole_client import PiholeClient, PiholeError
from reconcile import Reconciler
from results import ALL_CHANGES, Error, ErrorCode, SyncResult, classify

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/sync/pihole-config.yaml")
# Client definitions are site-specific, so they come from a separate file that
# each site's dns-config layer merges into the same ConfigMap. Deliberately
# required, not optional: a missing file would look like "no clients desired"
# and the reconciler would delete every client the site actually has.
CLIENTS_PATH = os.environ.get("CLIENTS_PATH", "/sync/pihole-clients.yaml")
STATE_DIR = os.environ.get("STATE_DIR", "/tmp/state")
_GRAVITY_FLAG = os.path.join(STATE_DIR, "gravity.flag")

# Exit statuses. Anything non-zero makes Kubernetes retry the Job.
_DONE = 0
_RETRY = 1


class Terminated(Exception):
    """Kubernetes asked the pod to stop: it was deleted, or ran out of time."""


def _on_sigterm(signum: int, frame: object) -> None:
    """Turn SIGTERM into an exception, so the `with` blocks close and the run is reported.

    Python's default for SIGTERM is to die on the spot, skipping both. This
    only works while Python is PID 1: sync-job.yaml `exec`s it for that reason.
    """
    raise Terminated(f"received {signal.Signals(signum).name}")


def main() -> int:
    """Run the sync, report how it went, and say whether to retry.

    Returns:
        The exit status: 0 when a restart can't change the outcome, success
        included, and non-zero when the Job should be retried.
    """
    signal.signal(signal.SIGTERM, _on_sigterm)
    started = time.monotonic()
    metrics = Metrics.from_environment(baseline=[change.attributes for change in ALL_CHANGES])
    result = SyncResult()
    try:
        status = _sync(metrics, result)
    except Terminated as exc:
        status = _fatal(result, ErrorCode.TERMINATED, exc, "Sync stopped by Kubernetes.", 128 + signal.SIGTERM)
    except Exception as exc:  # noqa: BLE001 - the catch-all: whatever nothing else handled
        status = _fatal(
            result,
            ErrorCode.UNKNOWN,
            exc,
            "Sync failed unexpectedly; exiting non-zero so the Job is retried.",
            _RETRY,
            with_backtrace=True,
        )
    # The same fact as the metrics, in the log, so each can be found from the other.
    log(
        "Sync finished.",
        event="run_finished",
        result="success" if result.ok else "failure",
        errors=len(result.errors),
        error_codes=sorted({str(e.code) for e in result.errors}),
        exit_status=status,
        duration_s=round(time.monotonic() - started),
    )
    metrics.record(result)  # pushed at exit, when the SDK shuts itself down
    return status


def _sync(metrics: Metrics, result: SyncResult) -> int:
    """Load the config, reconcile Pi-hole against it, and rebuild gravity if needed.

    Returns:
        The exit status, from whichever failure ended the run, or 0.
    """
    log(f"Pi-hole sync starting. Config={CONFIG_PATH} Clients={CLIENTS_PATH}")
    try:
        config = _load_config()
    except (OSError, yaml.YAMLError, TypeError, AttributeError) as exc:
        # The ConfigMap is wrong, and a restart reads the same one.
        return _fatal(
            result, ErrorCode.CONFIG, exc, "Cannot load config; exiting 0, a restart reads the same one.", _DONE
        )
    try:
        with PiholeClient(os.environ["PIHOLE_URL"], os.environ.get("PIHOLE_PASSWORD", ""), metrics) as pihole:
            Reconciler(pihole, config, result).run()
            return _rebuild_gravity(pihole, result)
    except PiholeError as exc:
        # Out of reach after every retry: Pi-hole may be restarting, so try the
        # Job again. Refused (a bad password, a request it rejects, an answer
        # we can't read): the next attempt would be refused the same way.
        if exc.transient:
            return _fatal(
                result, classify(exc), exc, "Pi-hole out of reach; exiting non-zero so the Job is retried.", _RETRY
            )
        return _fatal(result, classify(exc), exc, "Pi-hole refused the sync; exiting 0, a restart can't fix it.", _DONE)


def _load_config() -> dict[str, Any]:
    """Read the config, with the site's clients file under `clients`.

    Raises:
        OSError: If either file can't be read.
        yaml.YAMLError: If either isn't valid YAML.
        TypeError, AttributeError: If either isn't the expected mapping.
    """
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    with open(CLIENTS_PATH) as f:
        config["clients"] = (yaml.safe_load(f) or {}).get("clients") or []
    log(
        f"Config summary: {len(config.get('groups', []))} groups, "
        f"{len(config['clients'])} clients, "
        f"{len(config.get('allow_domains', []))} allow_domains, "
        f"{len(config.get('block_domains', []))} block_domains, "
        f"{len(config.get('allow_lists', []))} allow_lists, "
        f"{len(config.get('block_lists', []))} block_lists"
    )
    return config


def _rebuild_gravity(pihole: PiholeClient, result: SyncResult) -> int:
    """Rebuild gravity if this run or an earlier attempt in this pod needs it.

    The flag is written before the rebuild starts, so if it fails, or the pod
    is stopped part-way, the next attempt re-runs it though nothing else changed.

    Returns:
        The exit status: non-zero when the rebuild failed, so the Job retries it.
    """
    if os.path.exists(_GRAVITY_FLAG):
        log("Gravity flag present from a previous attempt: gravity will run.")
        result.gravity_required = True
    if not result.gravity_required:
        log("No gravity-triggering changes: skipping gravity update.")
        return _DONE
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_GRAVITY_FLAG, "w") as f:
        f.write("1")
    try:
        pihole.run_gravity()
    except PiholeError as exc:
        return _fatal(result, ErrorCode.GRAVITY, exc, "Gravity failed; exiting non-zero so the Job re-runs it.", _RETRY)
    os.remove(_GRAVITY_FLAG)
    return _DONE


def _fatal(
    result: SyncResult, code: ErrorCode, exc: Exception, message: str, status: int, *, with_backtrace: bool = False
) -> int:
    """Record the error that ended the run, and log the decision about the exit.

    Args:
        result: The run's result.
        code: What kind of failure it was.
        exc: The exception that ended the run.
        message: The log line: what happened, and what the exit status means.
        status: The exit status it leads to.
        with_backtrace: Include this program's frames, for a failure nothing
            handled, where the message alone doesn't say where it came from.

    Returns:
        `status`, so a caller can `return _fatal(...)`.
    """
    error = Error(
        code,
        str(exc),
        fatal=True,
        exception=type(exc).__name__,
        context={"exit_status": status},
        backtrace=backtrace(exc) if with_backtrace else None,
    )
    result.add_error(error, message)
    return status


if __name__ == "__main__":
    sys.exit(main())
