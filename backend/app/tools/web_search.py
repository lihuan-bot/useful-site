"""Web tools: Zhipu web search + fetch_url for downloading skill files."""

from __future__ import annotations

import logging

import httpx
from langchain_core.tools import tool

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@tool
def web_search(query: str) -> str:
    """Search the internet for up-to-date information.

    Args:
        query: The search query.
    """
    settings = get_settings()
    if not settings.zhipu_api_key:
        return "未配置：联网搜索服务尚未接入（需设置 ZHIPU_API_KEY），请等待后续版本。"

    try:
        from zai import ZhipuAiClient

        client = ZhipuAiClient(api_key=settings.zhipu_api_key)
        response = client.web_search.web_search(
            search_engine="search_pro",
            search_query=query,
            count=10,
            content_size="high",
        )
    except Exception as exc:
        logger.warning("web_search error: %s", exc)
        return f"搜索失败：{exc}"

    results = getattr(response, "search_result", None) or []
    if not results:
        return "未找到相关结果。"

    lines = [f"搜索「{query}」返回 {len(results)} 条结果：\n"]
    for i, item in enumerate(results, 1):
        title = getattr(item, "title", "") or ""
        content = getattr(item, "content", "") or ""
        link = getattr(item, "link", "") or ""
        media = getattr(item, "media", "") or ""
        lines.append(f"{i}. [{title}]({link})\n   来源：{media}\n   {content}\n")
    return "\n".join(lines)


@tool
def fetch_url(url: str) -> str:
    """Fetch the text content of a URL.

    Use this to download skill files (e.g. SKILL.md) from a URL provided
    by the user, then save the content with write_file.

    Args:
        url: The URL to fetch.
    """
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPStatusError as exc:
        return f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
    except Exception as exc:
        return f"fetch failed: {exc}"
