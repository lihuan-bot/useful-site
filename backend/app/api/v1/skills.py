"""Skill endpoints: CRUD SKILL.md files in RustFS.

Skills are stored as ``SKILL.md`` files (YAML frontmatter + Markdown body)
under ``users/{user_id}/skills/{skill_name}/SKILL.md`` in RustFS.  The
native ``SkillsMiddleware`` reads these files at agent build time and
injects their metadata into the system prompt via progressive disclosure.
"""

from __future__ import annotations

import re

import yaml
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.db.models import User
from app.schemas.skill import SkillCreate, SkillList, SkillOut, SkillUpdate
from app.services.storage import user_skills_prefix

router = APIRouter(prefix="/skills", tags=["skills"])


# ---------------------------------------------------------------------------
# SKILL.md helpers
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _build_skill_md(name: str, description: str, instructions: str) -> str:
    """Generate SKILL.md content from components."""
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        allow_unicode=True, default_flow_style=False, sort_keys=False,
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{instructions}"


def _parse_skill_md(content: str) -> tuple[str, str, str] | None:
    """Parse SKILL.md → (name, description, instructions). Returns None on bad format."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("name", "")).strip()
    desc = str(meta.get("description", "")).strip()
    if not name or not desc:
        return None
    return name, desc, m.group(2).strip()


def _skill_key(user_id: str, name: str) -> str:
    """S3 key for a skill's SKILL.md file."""
    return f"{user_skills_prefix(user_id)}/{name}/SKILL.md"


def _get_s3(request: Request) -> BaseClient:
    return request.app.state.s3


def _get_bucket() -> str:
    return get_settings().rustfs_bucket


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=SkillList)
def list_skills(
    request: Request,
    user: User = Depends(get_current_user),
) -> SkillList:
    s3 = _get_s3(request)
    bucket = _get_bucket()
    prefix = f"{user_skills_prefix(str(user.id))}/"

    # List all SKILL.md files under the user's skills prefix.
    skills: list[SkillOut] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if not obj["Key"].endswith("/SKILL.md"):
                continue
            try:
                raw = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                parsed = _parse_skill_md(raw.decode("utf-8"))
            except Exception:
                continue
            if parsed is None:
                continue
            name, desc, instr = parsed
            skills.append(SkillOut(name=name, description=desc, instructions=instr, path=obj["Key"]))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

    return SkillList(items=skills, total=len(skills))


@router.post("", response_model=SkillOut, status_code=201)
def create_skill(
    body: SkillCreate,
    request: Request,
    user: User = Depends(get_current_user),
) -> SkillOut:
    s3 = _get_s3(request)
    bucket = _get_bucket()
    key = _skill_key(str(user.id), body.name)

    # Check for duplicate.
    try:
        s3.head_object(Bucket=bucket, Key=key)
        raise HTTPException(status_code=409, detail=f"技能 '{body.name}' 已存在")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("404", "NoSuchKey"):
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                raise

    content = _build_skill_md(body.name, body.description, body.instructions)
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
    return SkillOut(
        name=body.name, description=body.description,
        instructions=body.instructions, path=key,
    )


@router.get("/{name}", response_model=SkillOut)
def get_skill(
    name: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> SkillOut:
    s3 = _get_s3(request)
    bucket = _get_bucket()
    key = _skill_key(str(user.id), name)
    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            raise HTTPException(status_code=404, detail="技能不存在")
        raise
    parsed = _parse_skill_md(raw.decode("utf-8"))
    if parsed is None:
        raise HTTPException(status_code=500, detail="SKILL.md 格式损坏")
    skill_name, desc, instr = parsed
    return SkillOut(name=skill_name, description=desc, instructions=instr, path=key)


@router.put("/{name}", response_model=SkillOut)
def update_skill(
    name: str,
    body: SkillUpdate,
    request: Request,
    user: User = Depends(get_current_user),
) -> SkillOut:
    s3 = _get_s3(request)
    bucket = _get_bucket()
    key = _skill_key(str(user.id), name)

    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            raise HTTPException(status_code=404, detail="技能不存在")
        raise

    parsed = _parse_skill_md(raw.decode("utf-8"))
    if parsed is None:
        raise HTTPException(status_code=500, detail="SKILL.md 格式损坏")
    _, old_desc, old_instr = parsed

    new_desc = body.description if body.description is not None else old_desc
    new_instr = body.instructions if body.instructions is not None else old_instr
    content = _build_skill_md(name, new_desc, new_instr)
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
    return SkillOut(name=name, description=new_desc, instructions=new_instr, path=key)


@router.delete("/{name}", status_code=204)
def delete_skill(
    name: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> None:
    s3 = _get_s3(request)
    bucket = _get_bucket()
    skill_prefix = f"{user_skills_prefix(str(user.id))}/{name}/"

    # Delete all objects under the skill directory.
    token = None
    deleted_any = False
    while True:
        kwargs = {"Bucket": bucket, "Prefix": skill_prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        objects = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            deleted_any = True
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

    if not deleted_any:
        raise HTTPException(status_code=404, detail="技能不存在")
