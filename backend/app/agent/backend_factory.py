"""Per-request backend assembly: warm sandbox + ``/files/`` RustFS route.

``build_backend_sync`` acquires a sandbox from the pre-warmed pool and wraps
it in a CompositeBackend: filesystem tools on paths under ``/files/`` go to
the user's persistent RustFS area, everything else (including ``execute``)
goes to the sandbox. MUST run in a worker thread — the pool is synchronous.

The user's persistent ``/skills/`` tree is additionally snapshotted into the
sandbox (see :func:`_inject_skills_into_sandbox`): filesystem tools see the
live RustFS copy, while ``execute`` — which always runs in the sandbox —
can run a skill's bundled scripts (``scripts/``, ``templates/``, ...).

The caller owns the sandbox lifecycle: any exit path must call ``akill()``
on the returned backend (see ``app/agent/runtime.py`` / chat endpoints).
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Tuple

from botocore.client import BaseClient

from app.backends.rustfs import RustFSBackend
from app.core.config import Settings
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol
from sandbox.pool import PreheatedSyncOpenSandboxBackend

logger = logging.getLogger(__name__)

# Injection caps: per bundled file and per request, so a huge accidental
# upload under /skills/ cannot stall every chat request.
SKILLS_INJECT_MAX_FILE_BYTES = 2 * 1024 * 1024
SKILLS_INJECT_MAX_TOTAL_BYTES = 20 * 1024 * 1024
_SKILLS_INJECT_WORKERS = 8


def _inject_skills_into_sandbox(
    *, s3: BaseClient, bucket: str, user_id: str, sandbox: PreheatedSyncOpenSandboxBackend
) -> None:
    """Snapshot the user's ``/skills/`` tree into the sandbox at ``/skills/``.

    Filesystem tools route ``/skills/`` paths to RustFS (persistent, live),
    but ``execute`` always runs in the sandbox — where the skills do not
    exist.  This lets a skill's bundled scripts run via shell commands.  The
    RustFS copy remains the source of truth; the snapshot is taken at
    request start.

    Failure is non-fatal: chat still works without injection (the agent
    just cannot execute skill scripts).
    """
    from app.services.storage import list_objects, user_skills_prefix

    started = time.perf_counter()
    prefix = user_skills_prefix(user_id)
    try:
        keys = [obj["Key"] for obj in list_objects(s3, bucket, prefix)]
    except Exception as exc:
        logger.warning("skills inject: list failed: %s", exc)
        return
    if not keys:
        return  # no skills — one LIST is the whole cost

    def _fetch(key: str) -> tuple[str, bytes | None]:
        try:
            raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception as exc:
            logger.warning("skills inject: read %s failed: %s", key, exc)
            return key, None
        return key, raw

    total = 0
    entries: list[tuple[str, bytes]] = []
    with ThreadPoolExecutor(max_workers=_SKILLS_INJECT_WORKERS) as ex:
        for key, raw in ex.map(_fetch, keys):
            if raw is None:
                continue
            if len(raw) > SKILLS_INJECT_MAX_FILE_BYTES:
                logger.warning(
                    "skills inject: skip %s (%d bytes > per-file cap)", key, len(raw)
                )
                continue
            total += len(raw)
            if total > SKILLS_INJECT_MAX_TOTAL_BYTES:
                logger.warning(
                    "skills inject: total cap reached, %d file(s) injected", len(entries)
                )
                break
            rel = key[len(prefix) + 1:]  # strip "users/{uid}/skills/"
            if not rel:
                continue  # an object exactly at the prefix, not a skill file
            entries.append((f"/skills/{rel}", raw))

    if not entries:
        return
    try:
        results = sandbox.upload_files(entries)
    except Exception as exc:
        logger.warning("skills inject: upload failed: %s", exc)
        return
    failed = [r.path for r in results if r.error]
    if failed:
        logger.warning(
            "skills inject: %d/%d file(s) failed to upload: %s",
            len(failed), len(entries), failed[:5],
        )
    logger.info(
        "skills inject: %d file(s) %d bytes → sandbox /skills/ (elapsed %.0fms)",
        len(entries) - len(failed), total, (time.perf_counter() - started) * 1000,
    )


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
    _inject_skills_into_sandbox(
        s3=s3, bucket=settings.rustfs_bucket, user_id=user_id, sandbox=sandbox,
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
    if sandbox is None:
        return
    kill = getattr(sandbox, "akill", None)
    if kill is not None:
        started = time.perf_counter()
        sandbox_id = getattr(sandbox, "id", "?")
        await kill()
        logger.info(
            "sandbox destroyed: id=%s elapsed=%.0fms",
            sandbox_id,
            (time.perf_counter() - started) * 1000,
        )
