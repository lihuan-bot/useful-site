"""Agent construction — the only module that touches deepagents types.

The compiled graph is built per request: ``backend`` (a fresh sandbox from
the pool) and ``tools`` (closures bound to the user id) are build-time
parameters. Compilation is cheap (<50ms). Shared singletons (ChatOpenAI,
checkpointer) are injected by the caller.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.prompts import AGENT_SYSTEM_PROMPT
from app.core.config import Settings, get_settings
from app.services.skill_md import parse_skill_md

# deepagents imports are intentionally confined to this layer.
from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol, FILE_NOT_FOUND
from langchain.agents.middleware import TodoListMiddleware

logger = logging.getLogger(__name__)


@lru_cache
def get_llm() -> ChatOpenAI:
    """Process-wide shared LLM client (stateless, thread-safe).

    Settings are fetched inside so the cache key stays arg-free (pydantic
    Settings instances are unhashable).
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
    )


def _log_skills_diagnostic(backend: BackendProtocol) -> None:
    """Pre-scan ``/skills/`` and log what ``SkillsMiddleware`` will see.

    Runs before agent construction so skill-loading failures surface
    immediately in logs instead of silently producing an agent with no
    skills.  Duplicates the middleware's ls + download_files calls but
    at INFO level with explicit per-file diagnostics.
    """
    import posixpath

    # 1) ls /skills/
    ls_result = backend.ls("/skills/")
    if getattr(ls_result, "error", None):
        logger.warning("skills: ls /skills/ failed: %s", ls_result.error)
        return

    entries = getattr(ls_result, "entries", None) or []
    skill_dirs = [e for e in entries if e.get("is_dir")]
    if not skill_dirs:
        logger.info("skills: /skills/ is empty — no skill directories found")
        return

    logger.info("skills: found %d directory(s) under /skills/", len(skill_dirs))

    # 2) download_files for each {dir}/SKILL.md
    skill_md_paths = [posixpath.join(e["path"], "SKILL.md") for e in skill_dirs]
    try:
        responses = backend.download_files(skill_md_paths)
    except NotImplementedError:
        logger.error(
            "skills: backend.download_files() not implemented — "
            "SkillsMiddleware will crash; skill loading disabled"
        )
        return
    except Exception:
        logger.exception("skills: backend.download_files() raised unexpectedly")
        return

    loaded = 0
    for resp in responses:
        if resp.error is not None:
            if resp.error == FILE_NOT_FOUND:
                logger.info("skills: %s — no SKILL.md (not a skill directory)", resp.path)
            else:
                logger.warning("skills: %s — download error: %s", resp.path, resp.error)
            continue
        if resp.content is None:
            logger.warning("skills: %s — downloaded but content is None", resp.path)
            continue
        # Parse via the shared SKILL.md parser (same logic as the skills API).
        try:
            text = resp.content.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("skills: %s — not valid UTF-8", resp.path)
            continue
        parsed = parse_skill_md(text)
        if not parsed.ok:
            logger.warning(
                "skills: %s — will be skipped by SkillsMiddleware: %s",
                resp.path, parsed.error,
            )
            continue
        logger.info(
            "skills: loaded '%s' (%d bytes) from %s — %s",
            parsed.name, len(resp.content), resp.path, parsed.description[:80],
        )
        loaded += 1

    logger.info("skills: %d/%d SKILL.md file(s) successfully parsed", loaded, len(skill_md_paths))


def build_agent(
    *,
    settings: Settings,
    llm: ChatOpenAI,
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    tools: list | None = None,
):
    """Build the per-request compiled deep agent graph.

    ``skills=["/skills/"]`` enables the native ``SkillsMiddleware`` which
    loads ``SKILL.md`` files from the ``/skills/`` RustFS route and injects
    their metadata into the system prompt via progressive disclosure.
    """
    # Diagnose skill loading before constructing the agent so failures
    # are visible in logs immediately.
    _log_skills_diagnostic(backend)

    middleware = [TodoListMiddleware()] if settings.agent_todos_enabled else None
    agent = create_deep_agent(
        model=llm,
        tools=tools or [],
        backend=backend,
        checkpointer=checkpointer,
        system_prompt=AGENT_SYSTEM_PROMPT,
        middleware=middleware,
        skills=["/skills/"],
    )
    logger.debug(
        "agent graph built: model=%s tools=%d todos=%s skills=enabled",
        settings.llm_model, len(tools or []), bool(middleware),
    )
    return agent
