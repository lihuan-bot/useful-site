"""FastAPI lifespan: startup/shutdown orchestration.

Startup order matters:
1. settings validation (fail fast)
2. SQLAlchemy engine (CRUD)
3. LangGraph checkpointer (AsyncPostgresSaver + psycopg async pool)
4. boto3 S3 client (RustFS)
5. OpenSandbox pool warmup (blocking — runs off the event loop)

Each resource is exposed via ``app.state``; the health endpoint degrades
gracefully when a resource is absent.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.db.session import init_engine

logger = logging.getLogger(__name__)


def _validate_settings(settings: Settings) -> None:
    """Fail fast on configuration that would break at request time."""
    # Required infrastructure endpoints/credentials (no sane defaults exist).
    required = {
        "DATABASE_DSN": settings.database_dsn,
        "RUSTFS_ENDPOINT": settings.rustfs_endpoint,
        "RUSTFS_ACCESS_KEY": settings.rustfs_access_key,
        "RUSTFS_SECRET_KEY": settings.rustfs_secret_key,
        "OPENSANDBOX_DOMAIN": settings.opensandbox_domain,
        "OPENSANDBOX_API_KEY": settings.opensandbox_api_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            f"missing required configuration: {', '.join(missing)} (see .env.example)"
        )
    if settings.jwt_secret == "change-me-in-production" and settings.env == "prod":
        raise RuntimeError("JWT_SECRET must be set in production")
    if settings.env == "prod" and not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY must be set in production")


def _build_limiter(settings: Settings):
    from app.backends.limiter import UserLimiter

    return UserLimiter(settings.max_concurrent_agents_per_user)


def _cleanup_orphan_sandboxes(settings: Settings) -> None:
    """Destroy all sandboxes on the OpenSandbox server before starting the pool.

    Previous processes (crashed or killed by --reload) leave orphaned sandboxes
    that the new pool cannot track. This sweeps them so only the fresh pool's
    sandboxes remain.
    """
    import json
    import urllib.request

    base = settings.opensandbox_domain.rstrip("/")
    headers = {"OPEN-SANDBOX-API-KEY": settings.opensandbox_api_key}

    try:
        req = urllib.request.Request(
            f"{base}/sandboxes?page=1&pageSize=200",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        items = data.get("items", data.get("sandboxes", [])) if isinstance(data, dict) else data
    except Exception:
        logger.warning("orphan cleanup: failed to list sandboxes, skipping")
        return

    if not items:
        logger.info("orphan cleanup: no sandboxes to clean")
        return

    destroyed = 0
    for s in items:
        sid = s.get("id", "")
        if not sid:
            continue
        try:
            req = urllib.request.Request(
                f"{base}/sandboxes/{sid}",
                method="DELETE",
                headers=headers,
            )
            urllib.request.urlopen(req, timeout=10)
            destroyed += 1
        except Exception:
            pass
    logger.info("orphan cleanup: destroyed %d/%d sandbox(es)", destroyed, len(items))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _validate_settings(settings)

    # 1) SQLAlchemy engine for CRUD endpoints.
    init_engine(settings)
    from app.db import session as db_session

    app.state.engine = db_session.engine
    logger.info("db engine ready: pool_max=%d", settings.db_pool_max)

    # 2) LangGraph checkpointer (Phase 3+). Fails startup on DB misconfig.
    from app.db.checkpointer import init_checkpointer

    app.state.checkpointer = await init_checkpointer(settings)

    # 3) boto3 S3 client for RustFS (Phase 6+).
    from app.services.storage import init_s3

    app.state.s3 = init_s3(settings)
    logger.info(
        "rustfs client ready: endpoint=%s bucket=%s",
        settings.rustfs_endpoint, settings.rustfs_bucket,
    )

    # 3b) RAG service (None when embedding is not configured).
    from app.rag.embeddings import EmbeddingsClient
    from app.rag.service import RAGService

    app.state.rag_service = None
    if settings.embedding_base_url:
        try:
            app.state.rag_service = RAGService(settings, EmbeddingsClient(settings))
            logger.info(
                "rag ready: model=%s dims=%d", settings.embedding_model, settings.embedding_dimensions
            )
        except Exception as exc:
            logger.warning("RAG disabled: %s", exc)
    else:
        logger.warning("rag disabled: EMBEDDING_BASE_URL not configured")
    app.state.user_limiter = _build_limiter(settings)
    app.state.active_streams = {}  # conversation_id -> asyncio.Task

    # 4) OpenSandbox pool warmup (Phase 4+). Blocking; must run off the loop.
    import asyncio

    from sandbox.pool import PreheatedSyncOpenSandboxBackend

    # Clean up orphaned sandboxes from previous (crashed) processes before
    # starting the pool — otherwise they accumulate across reloads.
    await asyncio.to_thread(
        _cleanup_orphan_sandboxes, settings
    )

    await asyncio.to_thread(
        PreheatedSyncOpenSandboxBackend.start_pool,
        domain=settings.opensandbox_domain,
        api_key=settings.opensandbox_api_key,
        image=settings.opensandbox_image,
        max_idle=settings.opensandbox_max_idle,
        use_server_proxy=settings.opensandbox_use_server_proxy,
        wait_for_warmup=True,
    )
    app.state.pool_snapshot = PreheatedSyncOpenSandboxBackend.pool_snapshot
    app.state.shutdown_pool = PreheatedSyncOpenSandboxBackend.shutdown_pool
    logger.info(
        "sandbox pool ready: max_idle=%d snapshot=%s",
        settings.opensandbox_max_idle,
        PreheatedSyncOpenSandboxBackend.pool_snapshot(),
    )

    logger.info(
        "startup complete: env=%s llm_model=%s log_level=%s",
        settings.env, settings.llm_model, settings.log_level,
    )
    yield

    # Shutdown: clean up in reverse order.
    # graceful=False: force-destroy idle sandboxes immediately.
    # In --reload mode uvicorn gives only ~5s before SIGKILL; waiting for
    # in-flight ops would leave orphan sandboxes on the server.
    if app.state.shutdown_pool is not None:
        await asyncio.to_thread(app.state.shutdown_pool, graceful=False)
    if app.state.checkpointer is not None:
        await app.state.checkpointer.aclose()
    if db_session.engine is not None:
        db_session.engine.dispose()
    logger.info("shutdown complete")
