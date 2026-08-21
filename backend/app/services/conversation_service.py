"""Conversation and message-mirror CRUD.

The mirror table is for fast UI listing; agent resume state lives in the
LangGraph checkpointer under thread_id ``{user_id}:{conversation_id}``.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import Conversation, Message, User


def thread_id_for(user_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    """Composite thread key — server-generated, never client-controlled."""
    return f"{user_id}:{conversation_id}"


def get_or_404(db: Session, conversation_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


def create_conversation(db: Session, user: User, title: str | None) -> Conversation:
    conv = Conversation(user_id=user.id, title=title or "新对话")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def list_conversations(
    db: Session, user_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[Conversation], int]:
    total = db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
    )
    rows = (
        db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return rows, total or 0


def add_message(
    db: Session,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    *,
    is_complete: bool = True,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        is_complete=is_complete,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def create_stream_message(db: Session, conversation_id: uuid.UUID) -> Message:
    """Insert the assistant placeholder row a stream updates in place.

    Created up front so a refreshed page immediately shows the "generating"
    bubble; the detached producer then updates content (throttled) and flips
    ``is_complete`` at the end via :func:`update_stream_message` /
    :func:`finalize_stream_message`.
    """
    msg = Message(
        conversation_id=conversation_id,
        role="assistant",
        content="",
        is_complete=False,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def update_stream_message(db: Session, message_id: uuid.UUID, content: str) -> None:
    """Throttled in-place content update while a stream is running."""
    db.execute(update(Message).where(Message.id == message_id).values(content=content))
    db.commit()


def finalize_stream_message(
    db: Session,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    content: str,
    is_complete: bool,
) -> None:
    """Final content + completeness flip; bumps the conversation's updated_at."""
    db.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(content=content, is_complete=is_complete)
    )
    db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=func.now())
    )
    db.commit()


def list_messages(
    db: Session, conversation_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[Message], int]:
    total = db.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.conversation_id == conversation_id)
    )
    rows = (
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return rows, total or 0


def delete_conversation(db: Session, conv: Conversation) -> None:
    """Delete the conversation row (messages cascade in the DB)."""
    db.delete(conv)
    db.commit()


def maybe_set_title(db: Session, conv: Conversation, content: str) -> None:
    """Title the conversation from its first user message."""
    if conv.title not in ("", "新对话"):
        return
    title = " ".join(content.split())[:30] or "新对话"
    db.execute(
        update(Conversation).where(Conversation.id == conv.id).values(title=title)
    )
    db.commit()


def touch_conversation(db: Session, conversation_id: uuid.UUID) -> None:
    """Bump updated_at (the onupdate trigger only fires on row updates)."""
    db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=func.now())
    )
    db.commit()
