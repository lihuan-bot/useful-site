"""RAG retrieval tool — factory binds the user id into the tool closure."""

from __future__ import annotations

from langchain_core.tools import tool

from app.rag.service import RAGService


def build_knowledge_search_tool(rag: RAGService, user_id: str):
    """Create a ``search_knowledge_base`` tool scoped to one user.

    The closure is the tenant boundary: the tool can only ever query chunks
    owned by ``user_id``.
    """

    @tool
    def search_knowledge_base(query: str, k: int = 5) -> str:
        """Search the user's uploaded documents (RAG knowledge base).

        Use this when the user's question references an uploaded file or asks
        about their personal documents. Returns the most relevant text chunks.

        Args:
            query: The search query (natural language).
            k: Number of chunks to return (default 5).
        """
        hits = rag.search(user_id=user_id, query=query, k=k)
        if not hits:
            return "知识库中未找到相关内容。"
        return "\n\n".join(
            f"[{h.filename} (score {h.score:.3f})]\n{h.text}" for h in hits
        )

    return search_knowledge_base
