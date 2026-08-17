"""Request/response schemas for documents."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    content_type: str | None
    size_bytes: int
    status: str
    error: str | None
    chunk_count: int
    conversation_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int
