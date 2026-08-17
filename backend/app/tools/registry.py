"""Per-request tool assembly."""

from __future__ import annotations

from app.rag.service import RAGService
from app.tools.knowledge_base import build_knowledge_search_tool
from app.tools.web_search import fetch_url, web_search


def build_tools(user, rag: RAGService | None) -> list:
    """Tools injected into the agent graph (closures bound to the user).

    The RAG tool is omitted when embedding is not configured.
    """
    tools: list = [web_search, fetch_url]
    if rag is not None:
        tools.append(build_knowledge_search_tool(rag, str(user.id)))
    return tools
