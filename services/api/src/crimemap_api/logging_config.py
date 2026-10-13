"""Structured JSON logs with the request ID. Never logs headers, bodies, or tokens."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from crimemap_api.http import request_id_var

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_REDACT_KEYS = ("authorization", "token", "password", "secret", "cookie")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD or key.startswith("_"):
                continue
            entry[key] = "[REDACTED]" if any(k in key.lower() for k in _REDACT_KEYS) else value
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only: messages can contain data.
            entry["exception"] = record.exc_info[0].__name__
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
