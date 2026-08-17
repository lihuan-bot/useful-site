"""Request/response schemas for conversations and chat."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class ConversationOut(BaseModel):
    id: UUID
    user_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationList(BaseModel):
    items: list[ConversationOut]
    total: int


class MessageOut(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    is_complete: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageList(BaseModel):
    items: list[MessageOut]
    total: int


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class ChatResponse(BaseModel):
    conversation_id: UUID
    message: MessageOut
