"""Skill endpoints: CRUD SKILL.md files in RustFS.

Skills are stored as ``SKILL.md`` files (YAML frontmatter + Markdown body)
under ``users/{user_id}/skills/{skill_name}/SKILL.md`` in RustFS.  The
native ``SkillsMiddleware`` reads these files at agent build time and
injects their metadata into the system prompt via progressive disclosure.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.core.deps import get_bucket, get_current_user, get_s3
from app.db.models import User
from app.schemas.skill import SkillCreate, SkillList, SkillOut, SkillUpdate
from app.services.skill_md import (
    ERROR_TOO_LARGE,
    MAX_SKILL_FILE_SIZE,
    build_skill_md,
    parse_skill_md,
    skill_key,
)
from app.services.storage import (
    is_s3_not_found,
    list_objects,
    object_exists,
    user_skills_prefix,
)

router = APIRouter(prefix="/skills", tags=["skills"])

# Max parallel GET requests when loading SKILL.md files in list_skills.
# boto3 clients are thread-safe for separate calls; 8 is a sane ceiling that
# avoids hammering RustFS while still cutting latency from N×RTT to ~1×RTT.
_SKILL_FETCH_WORKERS = 8


def _check_size(content: str) -> None:
    """Refuse to store a SKILL.md the middleware would refuse to load."""
    if len(content) > MAX_SKILL_FILE_SIZE:
        raise HTTPException(status_code=400, detail=ERROR_TOO_LARGE)


def _skill_dir_name(key: str) -> str:
    """Directory name of a SKILL.md key (``.../skills/foo/SKILL.md`` → ``foo``)."""
    return key.split("/")[-2]


def _broken_skill(key: str, load_error: str | None) -> SkillOut:
    """SkillOut for a SKILL.md that SkillsMiddleware would skip.

    Broken skills stay visible in list responses (with the reason) instead
    of silently vanishing from the agent's system prompt.
    """
    return SkillOut(
        name=_skill_dir_name(key),
        description="",
        instructions="",
        path=key,
        status="broken",
        load_error=load_error,
    )


def _skill_out_for(key: str, text: str | None) -> SkillOut:
    """Map one (key, body) pair to a SkillOut — ok or broken, never dropped."""
    if text is None:
        return _broken_skill(key, "无法读取 SKILL.md 文件")
    parsed = parse_skill_md(text)
    if not parsed.ok:
        return _broken_skill(key, parsed.error)
    return SkillOut(
        name=parsed.name, description=parsed.description,
        instructions=parsed.instructions, path=key,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=SkillList)
def list_skills(
    request: Request,
    user: User = Depends(get_current_user),
) -> SkillList:
    s3 = get_s3(request)
    bucket = get_bucket()
    prefix = f"{user_skills_prefix(str(user.id))}/"

    # Collect SKILL.md keys first (single LIST, paginated by list_objects).
    skill_keys = [
        obj["Key"]
        for obj in list_objects(s3, bucket, prefix)
        if obj["Key"].endswith("/SKILL.md")
    ]

    # Fetch all SKILL.md bodies in parallel — each get_object is an independent
    # network round-trip to RustFS, so concurrency turns N×RTT into ~1×RTT.
    skills: list[SkillOut] = []
    if not skill_keys:
        return SkillList(items=skills, total=0)

    def _fetch(key: str) -> tuple[str, str | None]:
        try:
            raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            return key, raw.decode("utf-8")
        except Exception:
            return key, None

    workers = min(_SKILL_FETCH_WORKERS, len(skill_keys))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for key, text in ex.map(_fetch, skill_keys):
            skills.append(_skill_out_for(key, text))

    return SkillList(items=skills, total=len(skills))


@router.post("", response_model=SkillOut, status_code=201)
def create_skill(
    body: SkillCreate,
    request: Request,
    user: User = Depends(get_current_user),
) -> SkillOut:
    s3 = get_s3(request)
    bucket = get_bucket()
    key = skill_key(str(user.id), body.name)

    if object_exists(s3, bucket, key):
        raise HTTPException(status_code=409, detail=f"技能 '{body.name}' 已存在")

    content = build_skill_md(body.name, body.description, body.instructions)
    _check_size(content)
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
    s3 = get_s3(request)
    bucket = get_bucket()
    key = skill_key(str(user.id), name)
    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        if is_s3_not_found(exc):
            raise HTTPException(status_code=404, detail="技能不存在")
        raise
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _broken_skill(key, "文件不是 UTF-8 编码")
    return _skill_out_for(key, text)


@router.put("/{name}", response_model=SkillOut)
def update_skill(
    name: str,
    body: SkillUpdate,
    request: Request,
    user: User = Depends(get_current_user),
) -> SkillOut:
    s3 = get_s3(request)
    bucket = get_bucket()
    key = skill_key(str(user.id), name)

    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        if is_s3_not_found(exc):
            raise HTTPException(status_code=404, detail="技能不存在")
        raise

    parsed = parse_skill_md(raw.decode("utf-8"))
    if not parsed.ok:
        raise HTTPException(status_code=500, detail=parsed.error or "SKILL.md 格式损坏")
    old_desc, old_instr = parsed.description, parsed.instructions

    new_desc = body.description if body.description is not None else old_desc
    new_instr = body.instructions if body.instructions is not None else old_instr
    content = build_skill_md(name, new_desc, new_instr)
    _check_size(content)
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
    return SkillOut(name=name, description=new_desc, instructions=new_instr, path=key)


@router.delete("/{name}", status_code=204)
def delete_skill(
    name: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> None:
    s3 = get_s3(request)
    bucket = get_bucket()
    skill_prefix = f"{user_skills_prefix(str(user.id))}/{name}/"

    # Delete all objects under the skill directory.
    keys = [obj["Key"] for obj in list_objects(s3, bucket, skill_prefix)]
    if not keys:
        raise HTTPException(status_code=404, detail="技能不存在")
    s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys]})


@router.post("/import", response_model=SkillOut, status_code=201)
async def import_skill(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> SkillOut:
    """从上传的文件导入技能。

    支持两种格式：
    1. 标准 SKILL.md（含 YAML frontmatter）→ 直接解析保存
    2. 普通 Markdown → 用文件名（去扩展名）作为 name，首段作为 description
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码不是 UTF-8")

    parsed = parse_skill_md(text)

    if parsed.ok:
        # 标准 SKILL.md 格式
        name, desc, instr = parsed.name, parsed.description, parsed.instructions
    else:
        # 超限文件无法被中间件加载，也没有兜底意义，直接拒绝。
        if parsed.error == ERROR_TOO_LARGE:
            raise HTTPException(status_code=400, detail=ERROR_TOO_LARGE)
        # 其余解析失败按普通 Markdown 兜底：从文件名推导 name，首段作为 description
        filename = file.filename or "imported-skill"
        name = re.sub(r"[^a-z0-9-]", "", filename.rsplit(".", 1)[0].lower()).strip("-")
        if not name:
            name = "imported-skill"
        # 首个非空段落作为 description
        lines = text.strip().splitlines()
        first_para = next((l for l in lines if l.strip().lstrip("#").strip()), "Imported skill")
        desc = first_para.strip().lstrip("#").strip()[:200]
        instr = text

    # 校验 name 格式
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        name = re.sub(r"[^a-z0-9-]", "", name.lower()).strip("-")
        if not name:
            name = "imported-skill"

    s3 = get_s3(request)
    bucket = get_bucket()
    key = skill_key(str(user.id), name)

    if object_exists(s3, bucket, key):
        raise HTTPException(status_code=409, detail=f"技能 '{name}' 已存在")

    content = build_skill_md(name, desc, instr)
    _check_size(content)
    s3.put_object(Bucket=bucket, Key=key, Body=content.encode("utf-8"))
    return SkillOut(name=name, description=desc, instructions=instr, path=key)
