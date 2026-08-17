"""Synchronous SQLAlchemy engine + session factory for CRUD.

FastAPI ``def`` endpoints run in a threadpool, so the sync engine is the
simplest reliable option. The LangGraph checkpointer uses its own psycopg
async pool (see ``app/db/checkpointer.py``) — the two are independent.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings

engine = None
SessionLocal: sessionmaker[Session] | None = None


def init_engine(settings: Settings) -> None:
    """Create the engine (called once at startup)."""
    global engine, SessionLocal
    engine = create_engine(
        settings.database_dsn,
        pool_size=settings.db_pool_max,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        },
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    assert SessionLocal is not None, "init_engine() must be called at startup"
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
