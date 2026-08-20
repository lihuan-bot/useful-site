"""SKILL.md file helpers shared by the skills API and the agent layer.

A SKILL.md file is YAML frontmatter (``name``, ``description``) followed by
a Markdown body (``instructions``).  Both the REST endpoints under
``app/api/v1/skills.py`` and the diagnostic scan in
``app/agent/factory.py`` parse this format, so the helpers live here to
avoid duplication.

Parsing rules mirror deepagents' ``SkillsMiddleware`` (the private
``_parse_skill_metadata`` in ``deepagents/middleware/skills.py``,
deepagents 0.7.6): a file the middleware would skip must come back with an
``error`` here — never silently accepted (the agent would never load it)
and never silently dropped (``list_skills`` surfaces broken skills).
``test/skill_parse_alignment.py`` verifies agreement against the installed
middleware on a battery of samples.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from app.services.storage import user_skills_prefix

# Mirrors deepagents.middleware.skills.MAX_SKILL_FILE_SIZE — the middleware
# measures ``len(content)`` on the decoded str (characters), so do we.
# test/skill_parse_alignment.py asserts equality with the installed constant.
MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)

# User-facing failure reasons.  An invalid name *format* is only a warning
# in the middleware (the skill still loads), so it is not an error here.
ERROR_TOO_LARGE = "技能内容超过 10MB 限制"
ERROR_NO_FRONTMATTER = "缺少 YAML frontmatter（文件需以 --- 开头）"
ERROR_BAD_FRONTMATTER = "frontmatter 不是合法的 YAML 映射"
ERROR_MISSING_METADATA = "frontmatter 缺少 name 或 description"


@dataclass(frozen=True)
class SkillParse:
    """Outcome of parsing one SKILL.md body.

    ``error is None`` means SkillsMiddleware would load this skill.  An
    error string explains why it would be skipped, for API responses and
    diagnostics.
    """

    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def parse_skill_md(content: str) -> SkillParse:
    """Parse SKILL.md → :class:`SkillParse`, mirroring SkillsMiddleware rules.

    Failure conditions match ``_parse_skill_metadata`` exactly: over-size
    content, missing frontmatter, bad YAML / non-mapping frontmatter, or
    empty ``name``/``description``.
    """
    if len(content) > MAX_SKILL_FILE_SIZE:
        return SkillParse(error=ERROR_TOO_LARGE)

    m = _FRONTMATTER_RE.match(content)
    if not m:
        return SkillParse(error=ERROR_NO_FRONTMATTER)
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return SkillParse(error=ERROR_BAD_FRONTMATTER)
    if not isinstance(meta, dict):
        return SkillParse(error=ERROR_BAD_FRONTMATTER)
    name = str(meta.get("name", "")).strip()
    desc = str(meta.get("description", "")).strip()
    if not name or not desc:
        return SkillParse(error=ERROR_MISSING_METADATA)
    return SkillParse(name=name, description=desc, instructions=m.group(2).strip())


def build_skill_md(name: str, description: str, instructions: str) -> str:
    """Generate SKILL.md content from components."""
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        allow_unicode=True, default_flow_style=False, sort_keys=False,
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{instructions}"


def skill_key(user_id: str, name: str) -> str:
    """S3 key for a skill's SKILL.md file."""
    return f"{user_skills_prefix(user_id)}/{name}/SKILL.md"
