"""Per-request backend assembly: warm sandbox + ``/files/`` RustFS route.

``build_backend_sync`` acquires a sandbox from the pre-warmed pool and wraps
it in a CompositeBackend: filesystem tools on paths under ``/files/`` go to
the user's persistent RustFS area, everything else (including ``execute``)
goes to the sandbox. MUST run in a worker thread — the pool is synchronous.

The caller owns the sandbox lifecycle: any exit path must call ``akill()``
on the returned backend (see ``app/agent/runtime.py`` / chat endpoints).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from botocore.client import BaseClient

from app.backends.rustfs import RustFSBackend
from app.core.config import Settings
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol
from sandbox.pool import PreheatedSyncOpenSandboxBackend

logger = logging.getLogger(__name__)


def build_backend_sync(
    settings: Settings, *, s3: BaseClient, user_id: str
) -> CompositeBackend:
    """Acquire a warm sandbox and compose the routed backend."""
    started = time.perf_counter()
    sandbox = PreheatedSyncOpenSandboxBackend.create(
        sandbox_timeout=timedelta(seconds=settings.sandbox_ttl_seconds),
    )
    logger.info(
        "sandbox acquired: id=%s elapsed=%.0fms",
        sandbox.id,
        (time.perf_counter() - started) * 1000,
    )
    rustfs = RustFSBackend(s3=s3, bucket=settings.rustfs_bucket, user_id=user_id)
    return CompositeBackend(default=sandbox, routes={"/files/": rustfs})


async def kill_backend(backend: BackendProtocol | None) -> None:
    """Release the request's sandbox back to the pool (idempotent)."""
    if backend is None:
        return
    kill = getattr(backend, "akill", None)
    if kill is not None:
        started = time.perf_counter()
        sandbox_id = getattr(backend.default, "id", "?")
        await kill()
        logger.info(
            "sandbox destroyed: id=%s elapsed=%.0fms",
            sandbox_id,
            (time.perf_counter() - started) * 1000,
        )
