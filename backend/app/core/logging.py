"""Logging setup and per-request context (request id / user id).

The formatter embeds ``request_id`` and ``user_id`` into every record via a
logging Filter; the values are carried in contextvars so they survive
asyncio task boundaries (e.g. into the SSE streaming generator).
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from app.core.config import Settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")

_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] [req=%(request_id)s user=%(user_id)s] %(message)s"

# Libraries that are chatty at INFO; keep app-level control over verbosity.
_QUIET_LOGGERS = (
    "httpx",
    "httpcore",
    "botocore",
    "urllib3",
    "boto3",
    "psycopg",
    "psycopg.pool",
    "alembic",
    "uvicorn.error",
)


class _ContextFilter(logging.Filter):
    """Injects request_id/user_id contextvars into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        return True


def setup_logging(settings: Settings) -> None:
    """Configure the root logger (idempotent; called once at app creation)."""
    level = settings.log_level.upper()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(_ContextFilter())

    root = logging.getLogger()
    root.handlers[:] = [handler]  # replace any basicConfig handlers
    root.setLevel(level)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Sandbox pool and agent internals follow the root level.
    logging.getLogger("deepagents").setLevel(level)
    logging.getLogger("sandbox").setLevel(level)
