"""Request logging middleware (pure ASGI — safe for SSE streaming).

For streaming responses (SSE) the duration covers the FULL stream: the
middleware's ``finally`` runs only after the response body finishes or the
client disconnects, so a long chat run is logged as one line with its real
duration and final status.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from app.core.logging import request_id_var

logger = logging.getLogger("app.request")


class RequestLoggingMiddleware:
    """Logs every HTTP request with id, duration, status; tags responses
    with an ``X-Request-ID`` header for client-side correlation."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status: int = 0
        errored = False

        async def send_wrapper(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"].append(
                    (b"x-request-id", request_id.encode("ascii"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except asyncio.CancelledError:
            logger.info(
                "%s %s -> client disconnected (%.0fms)",
                scope["method"], scope["path"],
                (time.perf_counter() - start) * 1000,
            )
            raise
        except Exception:
            errored = True
            logger.exception(
                "%s %s -> unhandled error", scope["method"], scope["path"]
            )
            raise
        finally:
            request_id_var.reset(token)
            if not errored:
                logger.info(
                    "%s %s -> %d (%.0fms)",
                    scope["method"], scope["path"], status,
                    (time.perf_counter() - start) * 1000,
                )
