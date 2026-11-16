"""Logging: one JSON object per line.

A line is parsed and filtered by field (`| json` in Loki) rather than matched by
text. Metrics say what happened; the detail, errors especially, is here.

Every error and warning line carries an `error` object (see `error_field`), so
all the exceptions can be shown with one filter.
"""

import json
import logging
import os
import sys
import time
import traceback

# This program's own files: a backtrace keeps only frames from here, dropping
# Python's and the installed libraries' (in the Job, /sync against /tmp/pydeps).
_PROGRAM_DIR = os.path.dirname(os.path.abspath(__file__))


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class JsonFormatter(logging.Formatter):
    """Writes each log record as one JSON object on one line."""

    def format(self, record):
        """Turn a log record into its JSON line.

        Args:
            record: The `logging.LogRecord`. Fields passed to `log` travel on it
                as `record.fields`.

        Returns:
            One JSON object: `time` (UTC, to the second), `level` (lower case),
            `msg`, and every extra field. A value that isn't JSON is written as
            its text, because a line that can't be written can't explain a
            failure.
        """
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
    """Write one log line.

    Args:
        msg: What happened. Surrounding whitespace is dropped.
        err: Log at error level.
        warn: Log at warning level. With neither `err` nor `warn`, the level is
            info.
        **fields: Extra JSON fields for the line, such as `method` and `path`.
            Use `error_field` to build the `error` object.
    """
    level = logging.ERROR if err else logging.WARNING if warn else logging.INFO
    logger.log(level, msg.strip(), extra={"fields": fields})


def error_field(code, message, *, exception=None, fatal=False, retry=None, backtrace=None):
    """Build the `error` object that every error and warning line carries.

    Args:
        code: An `ErrorCode`, or its string value. None for a warning that isn't
            a run's outcome, such as an attempt that will be retried; the key is
            then left out.
        message: The exception's message, or what went wrong.
        exception: The exception's class name, if there was an exception.
        fatal: True when the error ended the run.
        retry: `{"iteration": n, "max_iterations": m}` when the action will be
            tried again, and None when it won't, in which case the key is left
            out.

        backtrace: This program's frames (see `backtrace`), for a failure
            nothing handled.

    Returns:
        `{"error": {...}}`, ready to pass to `log` as `**error_field(...)`.
    """
    error = {"fatal": fatal, "message": message}
    if code:
        error["code"] = str(code)
    if backtrace:
        error["backtrace"] = backtrace
    if exception:
        error["exception"] = exception
    if retry:
        error["retry"] = retry
    return {"error": error}


def log_error(error, msg=None):
    """Log an `Error` at error level, with its context and its `error` object.

    Args:
        error: The `results.Error` to log. Its `context` (method, path, status
            and so on) becomes top-level fields on the line.
        msg: The line's message. Defaults to the error's own message.
    """
    fields = dict(error.context)
    fields.update(
        error_field(error.code, error.message, exception=error.exception, fatal=error.fatal, backtrace=error.backtrace)
    )
    log(msg or error.message, err=True, **fields)


def backtrace(exc):
    """Where an exception came from, in this program's own code.

    Frames in Python's standard library and the installed packages are left
    out: they say how a library failed, and the message already says that.

    Args:
        exc: The exception, after it was raised.

    Returns:
        One `file.py:line in function` per frame, outermost first.
    """
    return [
        f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
        if os.path.dirname(os.path.abspath(frame.filename)) == _PROGRAM_DIR
    ]
