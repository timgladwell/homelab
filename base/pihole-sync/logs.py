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
