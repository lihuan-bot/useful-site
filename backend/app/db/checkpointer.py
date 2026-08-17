"""LangGraph checkpointer singleton backed by PostgreSQL.

The checkpointer (AsyncPostgresSaver) persists agent threads — the resume
source of truth for conversations. It owns a dedicated psycopg async pool,
independent from the SQLAlchemy CRUD engine.

The checkpoint tables (checkpoints / checkpoint_writes / checkpoint_blobs)
are created by ``AsyncPostgresSaver.setup()``, not by Alembic.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings

logger = logging.getLogger(__name__)


class CheckpointerHandle:
    """AsyncPostgresSaver + its connection pool, closed together."""

    def __init__(self, saver: AsyncPostgresSaver, pool: AsyncConnectionPool) -> None:
        self.saver = saver
        self._pool = pool

    async def aclose(self) -> None:
        await self._pool.close()


async def init_checkpointer(settings: Settings) -> CheckpointerHandle:
    """Open the async pool, create the saver, and run setup() (idempotent).

    Connection semantics mirror ``AsyncPostgresSaver.from_conn_string``:
    autocommit on (the saver commits per statement; setup() migrations include
    ``CREATE INDEX CONCURRENTLY`` which cannot run inside a transaction block)
    and zero prepare threshold. The DSN is the SQLAlchemy URL
    (``postgresql+psycopg://``); psycopg's conninfo parser does not understand
    the ``+psycopg`` driver marker, so strip it before handing the string over.
    """
    conninfo = settings.database_dsn.replace("+psycopg", "")
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        open=False,
        # Startup waits for min_size connections; a flaky tunneled link can
        # take a while to settle — tolerate it instead of failing the app.
        timeout=60,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            # Detect dead connections quickly — the dev path to PG runs
            # through an SSH tunnel over a flaky ISP link.
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        },
    )
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    logger.info("checkpointer ready: pool min=%d max=%d", settings.db_pool_min, settings.db_pool_max)
    return CheckpointerHandle(saver=saver, pool=pool)
