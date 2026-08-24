"""Conversation CRUD and history endpoints."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_current_user, get_s3
from app.db.models import Conversation, User
from app.db.session import get_db
from app.services.storage import delete_prefix, user_artifacts_prefix
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationOut,
    MessageList,
    MessageOut,
)
from app.services import conversation_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=ConversationList)
async def list_conversations(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationList:
    rows, total = svc.list_conversations(db, user.id, limit=limit, offset=offset)
    flags = svc.latest_message_flags(db, [c.id for c in rows])

    # "streaming" = latest message is an unfinished assistant reply AND its
    # producer still holds the Redis reservation. "interrupted" = unfinished
    # but no lock (user stopped it, or the process died and the lock
    # expired) — lets the UI show a settled state instead of an endless
    # spinner. Redis outage degrades to DB-only flags.
    candidates = [str(c.id) for c in rows if not flags.get(c.id, True)]
    try:
        locks = await request.app.state.stream_store.active_locks(candidates)
    except Exception:
        logger.exception("streaming flags: redis check failed, falling back to DB only")
        locks = set()

    items = []
    for conv in rows:
        incomplete = not flags.get(conv.id, True)
        out = ConversationOut.model_validate(conv)
        items.append(out.model_copy(update={
            "streaming": incomplete and str(conv.id) in locks,
            "interrupted": incomplete and str(conv.id) not in locks,
        }))
    return ConversationList(items=items, total=total)


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(
    body: ConversationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Conversation:
    conv = svc.create_conversation(db, user, body.title)
    logger.info("conversation created: id=%s", conv.id)
    return conv


@router.get("/events")
async def conversation_events(
    request: Request,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE channel of this user's conversation-status transitions.

    One subscription per open page: producers publish
    ``conversation_status`` events (running / done / interrupted) so the
    conversation list updates in real time instead of polling. The list
    endpoint still provides the initial snapshot.
    """
    store = request.app.state.stream_store
    return StreamingResponse(
        store.follow_status(str(user.id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    conv = svc.get_or_404(db, conversation_id, user.id)
    messages, _ = svc.list_messages(db, conv.id, limit=200, offset=0)
    detail = ConversationDetail.model_validate(conv)
    detail.messages = [MessageOut.model_validate(m) for m in messages]
    return detail


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    conv = svc.get_or_404(db, conversation_id, user.id)
    svc.delete_conversation(db, conv)
    # Remove the agent thread from the checkpointer (all 3 checkpoint tables).
    checkpointer = request.app.state.checkpointer
    if checkpointer is not None:
        await checkpointer.saver.adelete_thread(svc.thread_id_for(user.id, conversation_id))
    # Remove middleware-offloaded artifacts (large tool results / evicted
    # conversation history) from RustFS. Best-effort: a storage blip must not
    # fail the delete; the startup sweep catches leftovers.
    try:
        s3 = get_s3(request)
        removed = delete_prefix(
            s3,
            get_settings().rustfs_bucket,
            user_artifacts_prefix(str(user.id), str(conversation_id)),
        )
        if removed:
            logger.info(
                "conversation artifacts deleted: id=%s objects=%d",
                conversation_id, removed,
            )
    except Exception:
        logger.exception("failed to delete conversation artifacts: id=%s", conversation_id)
    logger.info("conversation deleted: id=%s", conversation_id)


@router.get("/{conversation_id}/messages", response_model=MessageList)
def list_messages(
    conversation_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageList:
    conv = svc.get_or_404(db, conversation_id, user.id)
    rows, total = svc.list_messages(db, conv.id, limit=limit, offset=offset)
    return MessageList(items=rows, total=total)
