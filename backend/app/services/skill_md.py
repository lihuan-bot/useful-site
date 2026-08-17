"""SKILL.md file helpers shared by the skills API and the agent layer.

A SKILL.md file is YAML frontmatter (``name``, ``description``) followed by
a Markdown body (``instructions``).  Both the REST endpoints under
``app/api/v1/skills.py`` and the diagnostic scan in
``app/agent/factory.py`` need to parse this format, so the helpers live
here to avoid duplication.
"""

from __future__ import annotations

import re

import yaml

from app.services.storage import user_skills_prefix

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def build_skill_md(name: str, description: str, instructions: str) -> str:
    """Generate SKILL.md content from components."""
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        allow_unicode=True, default_flow_style=False, sort_keys=False,
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{instructions}"


def parse_skill_md(content: str) -> tuple[str, str, str] | None:
    """Parse SKILL.md → (name, description, instructions).

    Returns None on bad format (missing frontmatter, bad YAML, or empty
    name/description).
    """
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


def skill_key(user_id: str, name: str) -> str:
    """S3 key for a skill's SKILL.md file."""
    return f"{user_skills_prefix(user_id)}/{name}/SKILL.md"
