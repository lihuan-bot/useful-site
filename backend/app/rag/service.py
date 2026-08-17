"""RAG indexing and retrieval over pgvector."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from botocore.client import BaseClient
from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Chunk, Document
from app.rag.chunker import chunk_text
from app.rag.embeddings import EmbeddingsClient
from app.rag.parsers import parse_document

logger = logging.getLogger(__name__)


@dataclass
class Hit:
    filename: str
    text: str
    score: float


class RAGService:
    """Indexes uploaded documents and answers similarity queries."""

    def __init__(self, settings: Settings, embeddings: EmbeddingsClient) -> None:
        self._settings = settings
        self._embeddings = embeddings

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_document(
        self,
        *,
        db: Session,
        s3: BaseClient,
        doc: Document,
    ) -> None:
        """Parse → chunk → embed → insert. Runs in a background thread.

        ``doc`` must be a Document row (status transitions between
        'processing' and 'ready'/'failed' are managed by the caller —
        see ``app/services/document_service.py``).
        """
        settings = self._settings
        data = s3.get_object(Bucket=settings.rustfs_bucket, Key=doc.s3_key)[
            "Body"
        ].read()
        logger.debug(
            "indexing: document=%s fetched %d bytes from %s",
            doc.id, len(data), doc.s3_key,
        )
        text = parse_document(doc.filename, data)
        logger.debug("indexing: document=%s parsed %d chars", doc.id, len(text))
        chunks = chunk_text(
            text, chunk_size=settings.rag_chunk_size, chunk_overlap=settings.rag_chunk_overlap
        )
        if not chunks:
            raise ValueError("document produced no chunks")

        rows: list[Chunk] = []
        for i in range(0, len(chunks), settings.embedding_batch_size):
            batch = chunks[i : i + settings.embedding_batch_size]
            vectors = self._embeddings.embed(batch)
            logger.debug(
                "indexing: document=%s embedded batch %d-%d/%d",
                doc.id, i, i + len(batch), len(chunks),
            )
            for idx, (content, vector) in enumerate(zip(batch, vectors, strict=True)):
                rows.append(
                    Chunk(
                        document_id=doc.id,
                        user_id=doc.user_id,
                        chunk_index=i + idx,
                        content=content,
                        embedding=vector,
                    )
                )
        db.add_all(rows)
        db.commit()
        logger.info("indexed %d chunks for document %s", len(rows), doc.id)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def search(self, *, user_id: str, query: str, k: int | None = None) -> list[Hit]:
        """Cosine-similarity search over the user's ready documents."""
        from app.db.session import SessionLocal

        assert SessionLocal is not None
        k = k or self._settings.rag_max_results
        logger.debug("rag search: user=%s k=%d query=%r", user_id, k, query[:120])
        vector = self._embeddings.embed_one(query)
        sql = text(
            """
            SELECT d.filename, c.content,
                   1 - (c.embedding <=> :q) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.user_id = :uid AND d.status = 'ready'
            ORDER BY c.embedding <=> :q
            LIMIT :k
            """
        ).bindparams(
            bindparam("q", value=vector, type_=Vector(self._settings.embedding_dimensions)),
            bindparam("uid", value=uuid.UUID(user_id)),
            bindparam("k", value=k),
        )
        with SessionLocal() as db:
            rows = db.execute(sql).all()
        hits = [
            Hit(filename=row.filename, text=row.content, score=float(row.score))
            for row in rows
        ]
        logger.debug("rag search: user=%s hits=%d", user_id, len(hits))
        return hits
