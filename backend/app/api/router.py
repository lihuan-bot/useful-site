"""Aggregates all v1 routers under the configured prefix."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, chat, conversations, documents, files, health, skills

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(chat.router)
api_router.include_router(documents.router)
api_router.include_router(skills.router)
api_router.include_router(files.router)
