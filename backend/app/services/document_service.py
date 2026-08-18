"""Document CRUD + background indexing orchestration."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from botocore.client import BaseClient

from app.core.config import Settings
from app.db import session as db_session
from app.db.models import Chunk, Document, User
from app.rag.service import RAGService
from app.services.storage import user_document_key

logger = logging.getLogger(__name__)


def create_document(
    db: Session,
    user: User,
    *,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    conversation_id: uuid.UUID | None,
) -> Document:
    doc = Document(
        user_id=user.id,
        conversation_id=conversation_id,
        filename=filename,
        s3_key=user_document_key(str(user.id), "", filename),  # filled in below
        content_type=content_type,
        size_bytes=size_bytes,
        status="pending",
        chunk_count=0,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    doc.s3_key = user_document_key(str(user.id), str(doc.id), filename)
    db.commit()
    return doc


def get_or_404(db: Session, document_id: uuid.UUID, user_id: uuid.UUID) -> Document:
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def list_documents(
    db: Session, user_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[Document], int]:
    total = db.scalar(
        select(func.count()).select_from(Document).where(Document.user_id == user_id)
    )
    rows = (
        db.scalars(
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return rows, total or 0


def delete_document(db: Session, doc: Document, s3: BaseClient, bucket: str) -> None:
    """Delete chunks (DB cascade) + the S3 object + the row.

    A document that is still indexing is marked failed first so a racing
    background task stops writing chunks for it.
    """
    if doc.status in ("pending", "processing"):
        doc.status = "failed"
        db.commit()
    db.delete(doc)  # chunks cascade via FK
    db.commit()
    s3.delete_object(Bucket=bucket, Key=doc.s3_key)
    logger.info("document deleted: id=%s s3_key=%s", doc.id, doc.s3_key)


def run_indexing(
    doc_id: uuid.UUID,
    settings: Settings,
    *,
    s3: BaseClient,
    rag: RAGService,
) -> None:
    """Background task: parse → chunk → embed → store chunks.

    Runs in a threadpool thread; opens its own DB session but reuses the
    process-wide S3 client and RAGService singletons (created at lifespan
    startup) instead of rebuilding them per task.
    """
    assert db_session.SessionLocal is not None, "init_engine() must be called at startup"
    started = time.perf_counter()
    logger.info("indexing start: document=%s", doc_id)
    with db_session.SessionLocal() as db:
        doc = db.get(Document, doc_id)
        if doc is None or doc.status == "failed":
            return
        doc.status = "processing"
        db.commit()
        try:
            rag.index_document(db=db, s3=s3, doc=doc)
            doc.chunk_count = db.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == doc.id)
            )
            doc.status = "ready"
            doc.error = None
            db.commit()
            logger.info(
                "indexing done: document=%s chunks=%d elapsed=%.0fms",
                doc.id, doc.chunk_count, (time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            logger.exception("indexing failed for document %s", doc.id)
            db.rollback()
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.status = "failed"
                doc.error = str(exc)[:500]
                db.commit()
                logger.info(
                    "indexing failed (marked): document=%s error=%s", doc.id, doc.error[:120]
                )
