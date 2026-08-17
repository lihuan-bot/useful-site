"""Request/response schemas for auth endpoints."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")


class RegisterRequest(BaseModel):
    username: str
    password: str = Field(min_length=6, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not USERNAME_RE.fullmatch(v):
            raise ValueError(
                "username must be 3-32 chars: letters, digits, underscore, hyphen"
            )
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: UUID
    username: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RegisterResponse(BaseModel):
    id: UUID
    username: str
