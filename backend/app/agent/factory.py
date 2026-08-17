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

# deepagents imports are intentionally confined to this layer.
from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
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


def build_agent(
    *,
    settings: Settings,
    llm: ChatOpenAI,
    backend: BackendProtocol,
    checkpointer: BaseCheckpointSaver,
    tools: list | None = None,
):
    """Build the per-request compiled deep agent graph."""
    middleware = [TodoListMiddleware()] if settings.agent_todos_enabled else None
    agent = create_deep_agent(
        model=llm,
        tools=tools or [],
        backend=backend,
        checkpointer=checkpointer,
        system_prompt=AGENT_SYSTEM_PROMPT,
        middleware=middleware,
    )
    logger.debug(
        "agent graph built: model=%s tools=%d todos=%s",
        settings.llm_model, len(tools or []), bool(middleware),
    )
    return agent
