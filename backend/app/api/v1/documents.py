"""Document endpoints: upload (202 + background indexing), list, detail, delete."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_bucket, get_current_user, get_s3
from app.db.models import User
from app.db.session import get_db
from app.rag.parsers import SUPPORTED_EXTENSIONS
from app.schemas.document import DocumentList, DocumentOut
from app.services import document_service as svc

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=DocumentList)
def list_documents(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentList:
    rows, total = svc.list_documents(db, user.id, limit=limit, offset=offset)
    return DocumentList(items=rows, total=total)


@router.post("", response_model=DocumentOut, status_code=202)
def upload_document(
    background: BackgroundTasks,
    file: UploadFile,
    request: Request,
    conversation_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    settings = get_settings()
    if request.app.state.rag_service is None:
        raise HTTPException(
            status_code=503, detail="embedding service not configured"
        )
    filename = file.filename or "unnamed"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type: {ext or '(none)'}; supported: pdf, docx, txt",
        )
    data = file.file.read()
    if len(data) > settings.rag_max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large (max {settings.rag_max_file_bytes // (1024 * 1024)}MB)",
        )

    doc = svc.create_document(
        db,
        user,
        filename=filename,
        content_type=file.content_type,
        size_bytes=len(data),
        conversation_id=conversation_id,
    )

    # Upload the original file synchronously, then index in the background.
    try:
        s3 = get_s3(request)
        s3.put_object(Bucket=get_bucket(), Key=doc.s3_key, Body=data)
    except Exception as exc:
        doc.status = "failed"
        doc.error = f"upload failed: {exc}"
        db.commit()
        db.refresh(doc)
        return doc

    # Reuse the lifespan-singleton S3 client + RAGService instead of letting
    # the background task rebuild them per upload (each rebuild also poked
    # RustFS with a HEAD bucket request — pure waste).
    background.add_task(
        svc.run_indexing,
        doc.id,
        settings,
        s3=s3,
        rag=request.app.state.rag_service,
    )
    return doc


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    return svc.get_or_404(db, document_id, user.id)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    doc = svc.get_or_404(db, document_id, user.id)
    svc.delete_document(db, doc, get_s3(request), get_bucket())
