"""Per-request tool assembly."""

from __future__ import annotations

import uuid

from app.rag.service import RAGService
from app.tools.knowledge_base import build_knowledge_search_tool
from app.tools.order_form import build_submit_order_tool
from app.tools.web_search import fetch_url, web_search


def build_tools(user, rag: RAGService | None, conversation_id: uuid.UUID) -> list:
    """Tools injected into the agent graph (closures bound to the user).

    The RAG tool is omitted when embedding is not configured.
    """
    tools: list = [
        web_search,
        fetch_url,
        build_submit_order_tool(conversation_id),
    ]
    if rag is not None:
        tools.append(build_knowledge_search_tool(rag, str(user.id)))
    return tools
