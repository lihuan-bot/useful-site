"""Liveness / readiness probe and pool status endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(request: Request) -> dict:
    """No-auth liveness probe: reports DB connectivity and sandbox pool state."""
    settings = get_settings()

    db_ok = True
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    pool = None
    pool_fn = getattr(request.app.state, "pool_snapshot", None)
    if pool_fn is not None:
        pool = pool_fn()

    return {
        "status": "ok" if db_ok else "degraded",
        "env": settings.env,
        "db": db_ok,
        "pool": pool,
        "version": __version__,
    }


@router.get("/pool")
def pool_status(request: Request) -> dict:
    """Debug endpoint: current sandbox pool snapshot (dev only)."""
    if get_settings().env != "dev":
        raise HTTPException(status_code=404, detail="Not found")
    pool_fn = getattr(request.app.state, "pool_snapshot", None)
    if pool_fn is None:
        return {"state": "not_started"}
    return pool_fn()
