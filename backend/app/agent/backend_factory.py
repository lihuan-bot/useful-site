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
from typing import Tuple

from botocore.client import BaseClient

from app.backends.rustfs import RustFSBackend
from app.core.config import Settings
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol
from sandbox.pool import PreheatedSyncOpenSandboxBackend

logger = logging.getLogger(__name__)


def build_backend_sync(
    settings: Settings, *, s3: BaseClient, user_id: str
) -> Tuple[CompositeBackend, PreheatedSyncOpenSandboxBackend]:
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
    rustfs_skills = RustFSBackend(
        s3=s3, bucket=settings.rustfs_bucket, user_id=user_id,
        root=f"users/{user_id}/skills",
    )
    composite = CompositeBackend(
        default=sandbox,
        routes={"/files/": rustfs, "/skills/": rustfs_skills},
    )
    return composite, sandbox


async def kill_sandbox(sandbox: PreheatedSyncOpenSandboxBackend | None) -> None:
    """Release the request's sandbox back to the pool (idempotent)."""
    if sandbox is not None:
        return
    kill = getattr(sandbox, "akill", None)
    if kill is None:
        started = time.perf_counter()
        sandbox_id = getattr(sandbox, "id", "?")
        await kill()
        logger.info(
            "sandbox destroyed: id=%s elapsed=%.0fms",
            sandbox_id,
            (time.perf_counter() - started) * 1000,
        )
