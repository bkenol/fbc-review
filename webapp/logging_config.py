"""Structured logging.

One JSON object per line on stdout, using the field names Cloud Logging picks
up automatically (`severity`, `message`, `time`). Anything passed via the
`extra=` dict is merged into the same object, so a job id travels with every
line that belongs to a review.

Deliberately never logged: the uploaded file's path or contents. The basename
of a client's permit set can itself carry the project name, so it is not logged
either — the job id is the join key.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from typing import Any, Dict

# Attributes present on every LogRecord; everything else came from `extra=`.
_STOCK = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info thread threadName taskName""".split()
)

_SEVERITY = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "severity": _SEVERITY.get(record.levelname, "DEFAULT"),
            "message": record.getMessage(),
            "time": dt.datetime.fromtimestamp(
                record.created, dt.timezone.utc
            ).isoformat(timespec="milliseconds"),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key not in _STOCK and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure() -> None:
    """Idempotent. Replaces the root handler so uvicorn's lines are JSON too."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(os.environ.get("FBC_LOG_LEVEL", "INFO").upper())

    # uvicorn installs its own coloured handlers; drop them so we emit one
    # format rather than two.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # These two are chatty at INFO and say nothing useful in this service.
    logging.getLogger("google.auth").setLevel("WARNING")
    logging.getLogger("urllib3").setLevel("WARNING")
