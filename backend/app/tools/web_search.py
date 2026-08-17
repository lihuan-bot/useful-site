"""Web search tool — provider not wired up yet (stub)."""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def web_search(query: str) -> str:
    """Search the internet for up-to-date information.

    Args:
        query: The search query.

    NOTE: the search provider is not configured yet — returns a stub.
    Swap the body for a real provider call (Bocha / Tavily / SearXNG / ...)
    when one is chosen; the tool interface stays unchanged.
    """
    return "未配置：联网搜索服务尚未接入，请等待后续版本。"
