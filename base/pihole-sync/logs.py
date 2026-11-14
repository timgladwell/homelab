"""Logging: one JSON object per line.

A line is parsed and filtered by field (`| json` in Loki) rather than matched by
text. Metrics say what happened; the detail, errors especially, is here.

Every error and warning line carries an `error` object (see `error_field`), so
all the exceptions can be shown with one filter.
"""

import json
import logging
import sys
import time


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


def error_field(code, message, *, exception=None, fatal=False, retry=None):
    """Build the `error` object that every error and warning line carries.

    Args:
        code: An `ErrorCode`, or its string value.
        message: The exception's message, or what went wrong.
        exception: The exception's class name, if there was an exception.
        fatal: True when the error ended the run.
        retry: `{"iteration": n, "max_iterations": m}` when the action will be
            tried again, and None when it won't, in which case the key is left
            out.

    Returns:
        `{"error": {...}}`, ready to pass to `log` as `**error_field(...)`.
    """
    error = {"code": str(code), "fatal": fatal, "message": message}
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
    fields.update(error_field(error.code, error.message, exception=error.exception, fatal=error.fatal))
    log(msg or error.message, err=True, **fields)
