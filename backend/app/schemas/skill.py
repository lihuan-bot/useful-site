"""Request/response schemas for skills (file-based, no DB)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
                      description="技能名称：小写字母、数字、单连字符（如 json-to-table）")
    description: str = Field(..., min_length=1, max_length=1024, description="何时使用此技能")
    instructions: str = Field(..., min_length=1, description="技能的完整指令（Markdown）")


class SkillUpdate(BaseModel):
    description: str | None = Field(None, min_length=1, max_length=1024)
    instructions: str | None = Field(None, min_length=1)


class SkillOut(BaseModel):
    name: str
    description: str
    instructions: str
    path: str


class SkillList(BaseModel):
    items: list[SkillOut]
    total: int
