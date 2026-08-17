"""Shared FastAPI dependencies: DB session, current user, S3 client."""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """Validate the Bearer JWT and load the user row (401 on any failure)."""
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    settings = get_settings()
    user_id = decode_access_token(creds.credentials, secret=settings.jwt_secret)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    try:
        user = db.get(User, uuid.UUID(user_id))
    except ValueError:
        user = None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    # Attach the user to the request context so all logs from this request
    # (including the SSE stream) carry the user id.
    from app.core.logging import user_id_var

    user_id_var.set(str(user.id))
    return user


def get_s3(request: Request):
    """boto3 S3 client registered at startup."""
    return request.app.state.s3


def get_bucket() -> str:
    """RustFS bucket name from settings."""
    return get_settings().rustfs_bucket
