"""Chat endpoints: non-streaming single turn + SSE streaming.

Streaming is split into a detached **producer** task (runs the agent, owns
the sandbox / limiter slot / DB mirror) and per-connection **subscribers**
(replay the event log, then follow live events). A client disconnect — e.g.
a browser refresh — only removes its subscriber; generation continues
server-side and the refreshed page re-attaches via ``GET .../stream``.
All coordination goes through ``app.state.stream_store`` (Redis-backed,
see ``app/services/stream_store.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.backend_factory import build_backend_sync, kill_sandbox
from app.agent.factory import build_agent, get_llm
from app.agent.runtime import (
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
from app.schemas.conversation import ChatRequest, ChatResponse, MessageOut, ResumeRequest
from app.services import conversation_service as svc
from app.services.stream_store import STREAM_END, StreamStore
from app.tools.registry import build_tools

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Throttle for the incremental assistant-text mirror while a stream runs.
MIRROR_INTERVAL_SECONDS = 2.0
# Producer heartbeat: refreshes the reservation lock / per-user liveness.
HEARTBEAT_INTERVAL_SECONDS = 30.0

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
            tools=build_tools(user, request.app.state.rag_service, conversation_id),
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
    store: StreamStore,
    agent,
    sandbox,
    mapper: SSEEventMapper,
    conv_id: uuid.UUID,
    stream_msg_id: uuid.UUID,
    run_input,
    config: dict,
    thread_id: str,
    user_key: str,
) -> None:
    """Detached producer: run the agent and publish events to the store.

    Runs on its own task so a client disconnect (refresh) only kills the HTTP
    subscriber, never the generation. Three child tasks: the stream itself, a
    heartbeat (refreshes the reservation lock) and a stop listener (cross-
    worker /stop via the store). Cleanup lives in ``finally``: the END marker
    is published before the reservation is released, then sandbox, per-user
    slot and the DB mirror are freed. The producer uses its own DB session —
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

    # Notify the user's status channel: the conversation list lights up this
    # conversation's spinner without polling. Wrapped so a Redis blip right
    # at producer start doesn't kill the detached task without cleanup.
    try:
        await store.publish_status(
            user_key,
            sse("conversation_status", {"conversation_id": conv_key, "status": "running"}),
        )
    except Exception:
        logger.exception("failed to publish running status: thread=%s", thread_id)

    async def run_stream() -> None:
        nonlocal completed, last_mirror
        async for event in stream_agent(agent, mapper, run_input=run_input, config=config):
            if event.startswith(": keepalive"):
                # Keepalives are the subscriber's job; don't pollute the replay log.
                continue
            await store.publish(conv_key, event)
            now = time.perf_counter()
            if mapper.assistant_text and now - last_mirror >= MIRROR_INTERVAL_SECONDS:
                last_mirror = now
                await asyncio.to_thread(
                    svc.update_stream_message, mirror_db, stream_msg_id, mapper.assistant_text,
                )
        completed = True

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                await store.heartbeat(conv_key)
            except Exception:
                # A transient Redis error must not kill the refresher: with it
                # gone the reservation lock expires mid-run and single-flight /
                # subscriber liveness checks break.
                logger.warning("heartbeat refresh failed: thread=%s", thread_id, exc_info=True)

    stream_task = asyncio.create_task(run_stream())
    heartbeat_task = asyncio.create_task(heartbeat())
    stop_task = asyncio.create_task(store.wait_stop(conv_key))
    try:
        done, _ = await asyncio.wait(
            {stream_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done:
            stream_task.cancel()
            stream_exc = (await asyncio.gather(stream_task, return_exceptions=True))[0]
            if stream_exc and not isinstance(stream_exc, asyncio.CancelledError):
                logger.warning("agent stream error ignored after stop: %s", stream_exc)
            logger.warning(
                "agent stream cancelled: thread=%s elapsed=%.0fms",
                thread_id, (time.perf_counter() - started) * 1000,
            )
        else:
            stop_task.cancel()
            await stream_task  # raises on agent failure → outer except
    except asyncio.CancelledError:
        # Producer itself cancelled (server shutdown): fall through to cleanup.
        logger.warning("agent producer cancelled: thread=%s", thread_id)
    except Exception as exc:
        logger.exception("agent stream failed: thread=%s", thread_id)
        await store.publish(conv_key, mapper.error_event("agent_error", str(exc)[:300]))
    finally:
        heartbeat_task.cancel()
        stop_task.cancel()
        await asyncio.gather(heartbeat_task, stop_task, return_exceptions=True)
        try:
            await store.publish(conv_key, STREAM_END)
        except Exception:
            logger.exception("failed to publish stream end marker")
        try:
            await store.publish_status(
                user_key,
                sse(
                    "conversation_status",
                    {
                        "conversation_id": conv_key,
                        # HITL: a paused graph awaiting human input gets its
                        # own status so the list can show "等待补充".
                        "status": (
                            "done" if completed
                            else "awaiting_input" if mapper.interrupted
                            else "interrupted"
                        ),
                    },
                ),
            )
        except Exception:
            logger.exception("failed to publish conversation status")
        request.app.state.active_streams.pop(conv_key, None)
        try:
            await store.release(conv_key)
            await store.user_release(user_key, conv_key)
        except Exception:
            # A still-broken Redis must not abort sandbox/DB cleanup, nor
            # leave the user's ZSET slot stuck (until its 2h TTL) causing 429s.
            logger.exception("failed to release stream reservations: thread=%s", thread_id)
        await kill_sandbox(sandbox)
        final_text = mapper.assistant_text or "(no response)"
        if mapper.interrupted:
            final_text += "\n\n⏸ 等待补充信息"
        try:
            await asyncio.to_thread(
                svc.finalize_stream_message,
                mirror_db, conv_id, stream_msg_id,
                final_text, completed,
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
    store: StreamStore = request.app.state.stream_store
    user_key = str(user.id)
    settings = get_settings()

    # Single-flight per conversation (cross-worker via the store): a second
    # POST while one generation is running would mirror the user message
    # twice and fork the thread. Reserve BEFORE the user-cap check and hold
    # the reservation until the producer is running.
    if not await store.reserve(conv_key, user_key):
        raise HTTPException(status_code=409, detail="该会话已有进行中的生成")
    try:
        if not await store.user_acquire(user_key, conv_key, settings.max_concurrent_agents_per_user):
            raise HTTPException(status_code=429, detail="并发会话数已达上限，请稍候")
    except BaseException:
        await store.release(conv_key)
        raise

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
        # New generation id: subscribers replay ONLY this generation's
        # events, never the previous answer's log (or its END marker).
        await store.begin_generation(conv_key)
    except Exception:
        await store.user_release(user_key, conv_key)
        await store.release(conv_key)
        raise

    config = {"configurable": {"thread_id": thread_id}}
    mapper = SSEEventMapper(
        conversation_id=str(conv.id),
        thread_id=thread_id,
        base_url=str(request.base_url),
    )
    producer = asyncio.create_task(_produce_events(
        request=request,
        store=store,
        agent=agent,
        sandbox=sandbox,
        mapper=mapper,
        conv_id=conv.id,
        stream_msg_id=stream_msg.id,
        run_input={"messages": [{"role": "user", "content": user_content}]},
        config=config,
        thread_id=thread_id,
        user_key=user_key,
    ))
    # Local handle for shutdown cancellation; /stop goes through the store.
    request.app.state.active_streams[conv_key] = producer
    logger.info(
        "agent stream start: conversation=%s thread=%s content_len=%d images=%d vision=%s",
        conv.id, thread_id, len(body.content), len(body.image_paths or []), settings.llm_supports_vision,
    )
    return _sse_stream(store.follow(conv_key))


@router.get("/conversations/{conversation_id}/stream")
async def attach_stream(
    conversation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Re-attach to an in-flight generation (e.g. after a browser refresh).

    Replays the event log and follows live events — works from any worker
    when the store is Redis-backed. When nothing is running (or the producer
    is still spinning up), a single ``status`` event tells the client what
    to do: ``active=false`` → render from the messages endpoint;
    ``pending=true`` → retry in a moment.
    """
    svc.get_or_404(db, conversation_id, user.id)
    store: StreamStore = request.app.state.stream_store
    state = await store.state(str(conversation_id))

    if state != "active":
        async def status_only():
            yield sse("status", {"active": state != "inactive", "pending": state == "pending"})

        return _sse_stream(status_only)

    return _sse_stream(store.follow(str(conversation_id)))


@router.post("/conversations/{conversation_id}/resume")
async def resume_generation(
    conversation_id: uuid.UUID,
    body: ResumeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """HITL resume: continue a paused (interrupted) generation.

    The agent graph is paused at an ``interrupt()`` checkpoint; the human's
    answers arrive here and the same thread continues via
    ``Command(resume=...)``. The interrupted tool re-validates — if fields
    are still missing/invalid it interrupts AGAIN, otherwise it finishes and
    the agent keeps executing the rest of its flow. Streams exactly like a
    normal turn (new generation, same conversation).
    """
    conv = svc.get_or_404(db, conversation_id, user.id)
    conv_key = str(conv.id)
    store: StreamStore = request.app.state.stream_store
    user_key = str(user.id)
    settings = get_settings()

    if not await store.reserve(conv_key, user_key):
        raise HTTPException(status_code=409, detail="该会话已有进行中的生成")
    try:
        if not await store.user_acquire(user_key, conv_key, settings.max_concurrent_agents_per_user):
            raise HTTPException(status_code=429, detail="并发会话数已达上限，请稍候")
    except BaseException:
        await store.release(conv_key)
        raise

    try:
        # Mirror the human's补充 as a user message (display trace only — the
        # graph state itself resumes from the checkpoint).
        svc.add_message(
            db, conv.id, "user",
            f"[已补充信息] {json.dumps(body.answers, ensure_ascii=False)}",
        )
        svc.touch_conversation(db, conv.id)

        agent, sandbox, thread_id = await _prepare_run(request, user, conv.id)
        stream_msg = svc.create_stream_message(db, conv.id)
        await store.begin_generation(conv_key)
    except Exception:
        await store.user_release(user_key, conv_key)
        await store.release(conv_key)
        raise

    config = {"configurable": {"thread_id": thread_id}}
    mapper = SSEEventMapper(
        conversation_id=str(conv.id),
        thread_id=thread_id,
        base_url=str(request.base_url),
    )
    producer = asyncio.create_task(_produce_events(
        request=request,
        store=store,
        agent=agent,
        sandbox=sandbox,
        mapper=mapper,
        conv_id=conv.id,
        stream_msg_id=stream_msg.id,
        run_input=Command(resume=body.answers),
        config=config,
        thread_id=thread_id,
        user_key=user_key,
    ))
    request.app.state.active_streams[conv_key] = producer
    logger.info(
        "agent resume start: conversation=%s thread=%s answers=%d",
        conv.id, thread_id, len(body.answers),
    )
    return _sse_stream(store.follow(conv_key))


@router.post("/conversations/{conversation_id}/stop")
async def stop_generation(
    conversation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel an in-progress agent stream for this conversation.

    Cross-worker: the store notifies the producer regardless of which worker
    hosts it.
    """
    # Verify ownership.
    svc.get_or_404(db, conversation_id, user.id)

    store: StreamStore = request.app.state.stream_store
    conv_key = str(conversation_id)
    if not await store.is_active(conv_key):
        raise HTTPException(status_code=404, detail="没有正在进行的生成任务")

    await store.request_stop(conv_key)
    logger.info("stop requested: conversation=%s", conversation_id)
    return {"status": "cancelling"}
