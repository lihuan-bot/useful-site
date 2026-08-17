"""Pre-heated sandbox backend — acquires from a warm pool instead of creating on demand.

Architecture
------------

    PreheatedSyncOpenSandboxBackend.create()
      → pool.acquire()                          # grab from idle buffer (O(1))
          ├── idle hit  → SandboxSync.connect() # health-check only, ~2s
          └── idle miss → SandboxSync.create()  # fallback, ~30s

    Background reconcile loop (every 30s):
      while idle_count < max_idle:
          SandboxSync.create() → push to idle buffer

Zero asyncio, zero background event loops. The pool uses a ThreadPoolExecutor
for parallel warmup and a daemon thread for the reconcile loop.

Usage
-----

    # One-time setup (at process start)
    PreheatedSyncOpenSandboxBackend.start_pool(
        domain="http://124.221.180.74:10000",
        api_key="123456",
        image="...",
        max_idle=3,  # keep 3 sandboxes ready
    )

    # Every request — instant backend acquisition
    backend = PreheatedSyncOpenSandboxBackend.create()
    agent = create_deep_agent(model="...", backend=backend)
    result = agent.invoke({"messages": "..."})
    backend.kill()  # destroys this sandbox; pool replaces it in the background

    # At shutdown
    PreheatedSyncOpenSandboxBackend.shutdown_pool()
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import ClassVar

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox._pool_store import InMemoryPoolStateStore
from opensandbox.config.connection_sync import ConnectionConfigSync
from opensandbox.models.filesystem import WriteEntry
from opensandbox.pool_types import AcquirePolicy, PoolCreationSpec
from opensandbox.sync.pool import SandboxPoolSync
from opensandbox.sync.sandbox import SandboxSync

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "opensandbox/code-interpreter:v1.1.0"
DEFAULT_ENTRYPOINT = ["/opt/code-interpreter/code-interpreter.sh"]


class PreheatedSyncOpenSandboxBackend(BaseSandbox):
    """A `BaseSandbox` whose sandboxes come from a pre-warmed pool.

    Call :meth:`start_pool` once before creating backends, and
    :meth:`shutdown_pool` at process exit.

    ``create()`` acquires a ready sandbox — typically ~2 seconds (health
    check only), versus ~30 seconds for a cold start.
    """

    # ------------------------------------------------------------------
    # Shared pool state (class-level)
    # ------------------------------------------------------------------

    _pool: ClassVar[SandboxPoolSync | None] = None
    _pool_config: ClassVar[ConnectionConfigSync | None] = None

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def start_pool(
        cls,
        domain: str = "localhost:8080",
        image: str = DEFAULT_IMAGE,
        entrypoint: list[str] | None = None,
        api_key: str | None = None,
        *,
        max_idle: int = 3,
        warmup_concurrency: int | None = None,
        reconcile_interval: timedelta = timedelta(seconds=5),
        idle_timeout: timedelta = timedelta(minutes=10),
        ready_timeout: timedelta = timedelta(seconds=30),
        use_server_proxy: bool = False,
        pool_name: str = "deepagents-pool",
        wait_for_warmup: bool = True,
    ) -> None:
        """Start the background pool and pre-warm ``max_idle`` sandboxes.

        Call once at process startup. Subsequent calls are no-ops.

        Args:
            domain: OpenSandbox server address.
            image: Container image.
            entrypoint: Override the default entrypoint.
            api_key: ``OPEN-SANDBOX-API-KEY``.
            max_idle: Number of sandboxes to keep warm in the pool.
            warmup_concurrency: Max parallel warmup creates
                (default: min(max_idle, 10)).
            reconcile_interval: Seconds between pool replenishment checks.
            idle_timeout: How long an idle sandbox lives before being recycled.
            ready_timeout: Max wait for health check on acquire and warmup.
            use_server_proxy: Required on macOS / Docker Desktop.
            pool_name: Logical pool name (for metrics / debugging).
            wait_for_warmup: If True, block until at least one idle sandbox is ready.
        """
        if cls._pool is not None:
            return  # already started

        config = ConnectionConfigSync(
            domain=domain,
            api_key=api_key,
            use_server_proxy=use_server_proxy,
            request_timeout=timedelta(seconds=60),
        )

        creation_spec = PoolCreationSpec(
            image=image,
            entrypoint=entrypoint or DEFAULT_ENTRYPOINT,
        )

        # Small pools: full parallelism. Large pools: cap at 10 so a
        # 500-sandbox max_idle doesn't hammer the Docker daemon in one tick.
        if warmup_concurrency is None:
            warmup_concurrency = min(max_idle, 10)

        pool = SandboxPoolSync(
            pool_name=pool_name,
            max_idle=max_idle,
            warmup_concurrency=warmup_concurrency,
            reconcile_interval=reconcile_interval,
            connection_config=config,
            creation_spec=creation_spec,
            state_store=InMemoryPoolStateStore(),
            idle_timeout=idle_timeout,
            warmup_ready_timeout=ready_timeout,
            acquire_ready_timeout=ready_timeout,
        )
        pool.start()
        cls._pool = pool
        cls._pool_config = config
        logger.info(
            "Pool started: name=%s max_idle=%d warmup_concurrency=%d image=%s",
            pool_name,
            max_idle,
            warmup_concurrency,
            image,
        )

        if wait_for_warmup:
            cls._await_first_idle(ready_timeout)

    @classmethod
    def _await_first_idle(cls, timeout: timedelta) -> None:
        """Block until at least one sandbox is idle in the pool."""
        import time

        deadline = time.monotonic() + timeout.total_seconds()
        while time.monotonic() < deadline:
            snap = cls.pool_snapshot()
            if snap and snap["idle_count"] > 0:
                logger.info("Pool warmup: first idle sandbox ready (idle_count=%d)", snap["idle_count"])
                return
            time.sleep(1)
        logger.warning("Pool warmup: no idle sandbox after %ds", int(timeout.total_seconds()))

    @classmethod
    def shutdown_pool(cls, *, graceful: bool = True) -> None:
        """Stop the pool and release resources.

        Args:
            graceful: If True, drain in-flight operations before stopping.
        """
        if cls._pool is None:
            return
        cls._pool.shutdown(graceful=graceful)
        cls._pool = None
        cls._pool_config = None
        logger.info("Pool shut down.")

    @classmethod
    def pool_snapshot(cls) -> dict | None:
        """Return current pool status for monitoring, or None if not started."""
        if cls._pool is None:
            return None
        snap = cls._pool.snapshot()
        return {
            "state": snap.state.value,
            "lifecycle": snap.lifecycle_state.value,
            "idle_count": snap.idle_count,
            "max_idle": snap.max_idle,
            "in_flight": snap.in_flight_operations,
            "failures": snap.failure_count,
            "backoff": snap.backoff_active,
        }

    # ------------------------------------------------------------------
    # Backend factory
    # ------------------------------------------------------------------

    def __init__(self, sandbox: SandboxSync) -> None:
        self._sandbox = sandbox
        self.enable_capture_offload = True

    @classmethod
    def create(
        cls,
        *,
        sandbox_timeout: timedelta | None = None,
    ) -> "PreheatedSyncOpenSandboxBackend":
        """Acquire a sandbox from the pre-warmed pool.

        Requires :meth:`start_pool` to have been called first.

        Args:
            sandbox_timeout: Override TTL for the acquired sandbox.
        """
        if cls._pool is None:
            raise RuntimeError(
                "Pool not started. Call PreheatedSyncOpenSandboxBackend.start_pool() first."
            )
        sandbox = cls._pool.acquire(
            sandbox_timeout=sandbox_timeout,
            policy=AcquirePolicy.RETRY_NEXT_IDLE_THEN_CREATE,
        )
        return cls(sandbox)

    # ------------------------------------------------------------------
    # SandboxBackendProtocol (same as SyncOpenSandboxBackend)
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return self._sandbox.id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        from opensandbox.models.execd import RunCommandOpts

        opts = RunCommandOpts()
        if timeout is not None:
            opts.timeout = timedelta(seconds=timeout)

        execution = self._sandbox.commands.run(command, opts=opts)

        stdout = "\n".join(chunk.text for chunk in execution.logs.stdout)
        stderr = "\n".join(chunk.text for chunk in execution.logs.stderr)
        combined = "\n".join(part for part in (stdout, stderr) if part)

        return ExecuteResponse(output=combined, exit_code=execution.exit_code)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        invalid = [
            FileUploadResponse(path=p, error="invalid_path")
            for p, _ in files
            if not p.startswith("/")
        ]
        if invalid:
            return invalid

        entries = [WriteEntry(path=path, data=data, mode=644) for path, data in files]
        try:
            self._sandbox.files.write_files(entries)
            return [FileUploadResponse(path=p) for p, _ in files]
        except Exception as exc:
            return [FileUploadResponse(path=p, error=str(exc)) for p, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results: list[FileDownloadResponse] = []
        for path in paths:
            if not path.startswith("/"):
                results.append(FileDownloadResponse(path=path, error="invalid_path"))
                continue
            try:
                content = self._sandbox.files.read_bytes(path)
                results.append(FileDownloadResponse(path=path, content=content))
            except Exception as exc:
                results.append(
                    FileDownloadResponse(
                        path=path,
                        error=self._classify_download_error(path, exc),
                    )
                )
        return results

    def _classify_download_error(self, path: str, exc: Exception) -> str:
        try:
            info = self._sandbox.files.get_file_info([path])
        except Exception:
            info = None
        entry = info.get(path) if info else None
        if entry is None:
            return "file_not_found"
        if entry.entry_type and "dir" in entry.entry_type.lower():
            return "is_directory"
        if entry.mode == 0:
            return "permission_denied"
        text = str(exc)
        if "FILE_NOT_FOUND" in text or "404" in text:
            return "file_not_found"
        return text

    def kill(self) -> None:
        """Destroy this sandbox. The pool replaces it in the background."""
        self._sandbox.destroy()

    async def akill(self) -> None:
        import asyncio
        await asyncio.to_thread(self.kill)
