"""Auth endpoints: register / login / me."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
) -> RegisterResponse:
    existing = db.scalar(select(User).where(User.username == body.username))
    if existing is not None:
        logger.info("register rejected: username=%s (already taken)", body.username)
        raise HTTPException(status_code=409, detail="Username already taken")
    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("user registered: id=%s username=%s", user.id, user.username)
    return RegisterResponse(id=user.id, username=user.username)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    # Uniform 401 for bad username or bad password (no user enumeration).
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        logger.info("login failed: username=%s", body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(
        str(user.id),
        secret=settings.jwt_secret,
        expire_minutes=settings.jwt_expire_minutes,
    )
    logger.info("login ok: id=%s username=%s", user.id, user.username)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
