"""Chat endpoints: non-streaming single turn + SSE streaming."""

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
from app.agent.runtime import SSEEventMapper, build_multimodal_input, run_agent, stream_agent
from app.core.config import get_settings
from app.core.deps import get_current_user, get_s3
from app.db.models import User
from app.db.session import get_db
from app.schemas.conversation import ChatRequest, ChatResponse, MessageOut
from app.services import conversation_service as svc
from app.tools.registry import build_tools

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


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


@router.post("/conversations/{conversation_id}/stream")
async def stream_chat(
    conversation_id: uuid.UUID,
    body: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    conv = svc.get_or_404(db, conversation_id, user.id)
    limiter = request.app.state.user_limiter
    user_key = str(user.id)
    if not await limiter.try_acquire(user_key):
        raise HTTPException(status_code=429, detail="已有进行中的会话，请稍候")

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
    except Exception:
        limiter.release(user_key)
        raise
    config = {"configurable": {"thread_id": thread_id}}
    mapper = SSEEventMapper(
        conversation_id=str(conv.id),
        thread_id=thread_id,
        base_url=str(request.base_url),
    )

    async def gen():
        # Register this task so the stop endpoint can cancel it.
        task = asyncio.current_task()
        request.app.state.active_streams[str(conv.id)] = task

        started = time.perf_counter()
        logger.info(
            "agent stream start: conversation=%s thread=%s content_len=%d images=%d vision=%s",
            conv.id, thread_id, len(body.content), len(body.image_paths or []), settings.llm_supports_vision,
        )
        completed = False
        try:
            async for event in stream_agent(agent, mapper, content=user_content, config=config):
                yield event
            completed = True
        except asyncio.CancelledError:
            # Client disconnected or /stop was called: stop streaming; the
            # finally block releases the sandbox and the limiter.
            logger.warning(
                "agent stream cancelled: thread=%s elapsed=%.0fms",
                thread_id, (time.perf_counter() - started) * 1000,
            )
            raise
        except Exception as exc:
            logger.exception("agent stream failed: thread=%s", thread_id)
            yield mapper.error_event("agent_error", str(exc)[:300])
        finally:
            request.app.state.active_streams.pop(str(conv.id), None)
            await kill_sandbox(sandbox)
            limiter.release(user_key)
            logger.info(
                "agent stream end: thread=%s elapsed=%.0fms complete=%s reply_len=%d",
                thread_id, (time.perf_counter() - started) * 1000,
                completed, len(mapper.assistant_text),
            )

        # Mirror the assistant message (best-effort; a cancelled stream
        # still writes what was produced so far with is_complete=False).
        try:
            text = mapper.assistant_text or "(no response)"
            svc.add_message(db, conv.id, "assistant", text, is_complete=completed)
            svc.touch_conversation(db, conv.id)
        except Exception:
            logger.exception("failed to mirror assistant message")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
