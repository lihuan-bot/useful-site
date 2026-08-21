"""Chat endpoints: non-streaming single turn + SSE streaming.

Streaming is split into a detached **producer** task (runs the agent, owns
the sandbox / limiter slot / DB mirror) and per-connection **subscribers**
(replay the broker buffer, then follow live events). A client disconnect —
e.g. a browser refresh — only removes its subscriber; generation continues
server-side and the refreshed page re-attaches via ``GET .../stream``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent.backend_factory import build_backend_sync, kill_sandbox
from app.agent.factory import build_agent, get_llm
from app.agent.runtime import (
    KEEPALIVE_SECONDS,
    SSEEventMapper,
    build_multimodal_input,
    run_agent,
    sse,
    stream_agent,
)
from app.core.config import get_settings
from app.core.deps import get_current_user, get_s3
from app.db import session as db_session
from app.db.models import User
from app.db.session import get_db
from app.schemas.conversation import ChatRequest, ChatResponse, MessageOut
from app.services import conversation_service as svc
from app.services.stream_broker import StreamBroker
from app.tools.registry import build_tools

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Throttle for the incremental assistant-text mirror while a stream runs.
MIRROR_INTERVAL_SECONDS = 2.0

_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse_stream(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream", headers=_STREAM_HEADERS)


async def _prepare_run(request: Request, user: User, conversation_id: uuid.UUID):
    """Shared setup: ownership check, backend acquisition, graph build.

    Returns (agent, backend, thread_id, settings). The caller MUST kill the
    backend on every exit path.
    """
    settings = get_settings()
    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="LLM not configured")

    backend, sandbox = await asyncio.to_thread(
        build_backend_sync,
        settings,
        s3=get_s3(request),
        user_id=str(user.id),
    )
    try:
        agent = build_agent(
            settings=settings,
            llm=get_llm(),
            backend=backend,
            checkpointer=request.app.state.checkpointer.saver,
            tools=build_tools(user, request.app.state.rag_service),
        )
    except Exception:
        await kill_sandbox(sandbox)
        raise
    thread_id = svc.thread_id_for(user.id, conversation_id)
    return agent, sandbox, thread_id


@router.post("/conversations/{conversation_id}/messages", response_model=ChatResponse)
async def send_message(
    conversation_id: uuid.UUID,
    body: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    conv = svc.get_or_404(db, conversation_id, user.id)

    # Mirror the user message and set the conversation title on first message.
    svc.add_message(db, conv.id, "user", body.content)
    svc.maybe_set_title(db, conv, body.content)
    svc.touch_conversation(db, conv.id)

    settings = get_settings()
    user_content = await build_multimodal_input(
        body.content,
        body.image_paths,
        user_id=str(user.id),
        s3_client=get_s3(request),
        bucket=settings.rustfs_bucket,
        supports_vision=settings.llm_supports_vision,
    )

    agent, sandbox, thread_id = await _prepare_run(request, user, conv.id)
    config = {"configurable": {"thread_id": thread_id}}
    started = time.perf_counter()
    logger.info(
        "agent run start: conversation=%s thread=%s mode=invoke content_len=%d images=%d vision=%s",
        conv.id, thread_id, len(body.content), len(body.image_paths or []), settings.llm_supports_vision,
    )
    try:
        result = await run_agent(agent, content=user_content, config=config)
    finally:
        await kill_sandbox(sandbox)
    logger.info(
        "agent run done: thread=%s elapsed=%.0fms reply_len=%d",
        thread_id, (time.perf_counter() - started) * 1000, len(result.text),
    )

    msg = svc.add_message(
        db, conv.id, "assistant", result.text or "(no response)",
        is_complete=not result.interrupted,
    )
    svc.touch_conversation(db, conv.id)
    return ChatResponse(conversation_id=conv.id, message=MessageOut.model_validate(msg))


async def _produce_events(
    *,
    request: Request,
    agent,
    sandbox,
    mapper: SSEEventMapper,
    broker: StreamBroker,
    conv_id: uuid.UUID,
    stream_msg_id: uuid.UUID,
    user_content,
    config: dict,
    thread_id: str,
    user_key: str,
) -> None:
    """Detached producer: run the agent and fan events out to the broker.

    Runs on its own task so a client disconnect (refresh) only kills the HTTP
    subscriber, never the generation. Cleanup lives in ``finally``: sandbox,
    limiter slot and the DB mirror are held for the lifetime of the
    generation, not of the connection. The producer uses its own DB session —
    the request-scoped one closes when the response ends.
    """
    conv_key = str(conv_id)
    # Module-attribute access: a top-level ``from app.db.session import
    # SessionLocal`` would snapshot None (startup hasn't run at import time).
    assert db_session.SessionLocal is not None, "init_engine() must be called at startup"
    mirror_db = db_session.SessionLocal()
    started = time.perf_counter()
    completed = False
    last_mirror = 0.0
    try:
        async for event in stream_agent(agent, mapper, content=user_content, config=config):
            if event.startswith(": keepalive"):
                # Keepalives are the subscriber's job; don't pollute the replay buffer.
                continue
            broker.publish(event)
            now = time.perf_counter()
            if mapper.assistant_text and now - last_mirror >= MIRROR_INTERVAL_SECONDS:
                last_mirror = now
                await asyncio.to_thread(
                    svc.update_stream_message, mirror_db, stream_msg_id, mapper.assistant_text,
                )
        completed = True
    except asyncio.CancelledError:
        # /stop or server shutdown: stop generating. The finally block still
        # mirrors the partial text (is_complete=False) and releases resources.
        logger.warning(
            "agent stream cancelled: thread=%s elapsed=%.0fms",
            thread_id, (time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        logger.exception("agent stream failed: thread=%s", thread_id)
        broker.publish(mapper.error_event("agent_error", str(exc)[:300]))
    finally:
        broker.finish()
        request.app.state.active_streams.pop(conv_key, None)
        request.app.state.stream_brokers.pop(conv_key, None)
        await kill_sandbox(sandbox)
        request.app.state.user_limiter.release(user_key)
        try:
            await asyncio.to_thread(
                svc.finalize_stream_message,
                mirror_db, conv_id, stream_msg_id,
                mapper.assistant_text or "(no response)", completed,
            )
        except Exception:
            logger.exception("failed to finalize assistant message")
        finally:
            mirror_db.close()
        logger.info(
            "agent stream end: thread=%s elapsed=%.0fms complete=%s reply_len=%d",
            thread_id, (time.perf_counter() - started) * 1000,
            completed, len(mapper.assistant_text),
        )


async def _follow_broker(broker: StreamBroker):
    """SSE generator: replay buffered events, then follow live ones.

    Emits its own keepalives so the connection stays warm during long tool
    runs, and unsubscribes on disconnect (the producer keeps running).
    """
    replay, queue = broker.subscribe()
    try:
        for event in replay:
            yield event
        if broker.closed:
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is None:  # producer finished
                return
            yield event
    finally:
        broker.unsubscribe(queue)


@router.post("/conversations/{conversation_id}/stream")
async def stream_chat(
    conversation_id: uuid.UUID,
    body: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    conv = svc.get_or_404(db, conversation_id, user.id)
    conv_key = str(conv.id)
    brokers = request.app.state.stream_brokers

    # Single-flight per conversation: a second POST while one generation is
    # running would mirror the user message twice and fork the thread.
    # Check + reserve are atomic (no await in between): concurrent POSTs
    # can't both slip through. The reservation is swapped for the real
    # broker once the producer is ready; GET attaches while the entry is
    # None see pending=true and retry.
    if conv_key in brokers:
        raise HTTPException(status_code=409, detail="该会话已有进行中的生成")
    brokers[conv_key] = None

    limiter = request.app.state.user_limiter
    user_key = str(user.id)
    if not await limiter.try_acquire(user_key):
        brokers.pop(conv_key, None)
        raise HTTPException(status_code=429, detail="并发会话数已达上限，请稍候")

    settings = get_settings()
    user_content = await build_multimodal_input(
        body.content,
        body.image_paths,
        user_id=str(user.id),
        s3_client=get_s3(request),
        bucket=settings.rustfs_bucket,
        supports_vision=settings.llm_supports_vision,
    )

    try:
        # Mirror the user message up front.
        svc.add_message(db, conv.id, "user", body.content)
        svc.maybe_set_title(db, conv, body.content)
        svc.touch_conversation(db, conv.id)

        agent, sandbox, thread_id = await _prepare_run(request, user, conv.id)
        stream_msg = svc.create_stream_message(db, conv.id)
    except Exception:
        brokers.pop(conv_key, None)
        limiter.release(user_key)
        raise

    config = {"configurable": {"thread_id": thread_id}}
    mapper = SSEEventMapper(
        conversation_id=str(conv.id),
        thread_id=thread_id,
        base_url=str(request.base_url),
    )
    broker = StreamBroker()
    brokers[conv_key] = broker
    producer = asyncio.create_task(_produce_events(
        request=request,
        agent=agent,
        sandbox=sandbox,
        mapper=mapper,
        broker=broker,
        conv_id=conv.id,
        stream_msg_id=stream_msg.id,
        user_content=user_content,
        config=config,
        thread_id=thread_id,
        user_key=user_key,
    ))
    request.app.state.active_streams[conv_key] = producer
    logger.info(
        "agent stream start: conversation=%s thread=%s content_len=%d images=%d vision=%s",
        conv.id, thread_id, len(body.content), len(body.image_paths or []), settings.llm_supports_vision,
    )
    return _sse_stream(_follow_broker(broker))


@router.get("/conversations/{conversation_id}/stream")
async def attach_stream(
    conversation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Re-attach to an in-flight generation (e.g. after a browser refresh).

    Replays buffered events and follows live ones. When nothing is running
    (or the producer is still spinning up), a single ``status`` event tells
    the client what to do: ``active=false`` → render from the messages
    endpoint; ``pending=true`` → retry in a moment.
    """
    svc.get_or_404(db, conversation_id, user.id)
    brokers = request.app.state.stream_brokers
    conv_key = str(conversation_id)
    broker = brokers.get(conv_key)

    if broker is None:
        pending = conv_key in brokers  # reserved, producer not up yet

        async def status_only():
            yield sse("status", {"active": pending, "pending": pending})

        return _sse_stream(status_only)

    return _sse_stream(_follow_broker(broker))


@router.post("/conversations/{conversation_id}/stop")
async def stop_generation(
    conversation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel an in-progress agent stream for this conversation."""
    # Verify ownership.
    svc.get_or_404(db, conversation_id, user.id)

    task = request.app.state.active_streams.get(str(conversation_id))
    if task is None:
        raise HTTPException(status_code=404, detail="没有正在进行的生成任务")

    task.cancel()
    logger.info("stop requested: conversation=%s", conversation_id)
    return {"status": "cancelling"}
