"""OpenAI-compatible embeddings client (httpx, no LangChain dependency)."""

from __future__ import annotations

import httpx

from app.core.config import Settings


class EmbeddingError(Exception):
    """Raised when the embedding endpoint fails or returns wrong dimensions."""


class EmbeddingsClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.embedding_base_url:
            raise EmbeddingError("EMBEDDING_BASE_URL is not configured")
        self._url = settings.embedding_base_url.rstrip("/") + "/embeddings"
        self._api_key = settings.embedding_api_key
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (network call, blocking — call in a thread)."""
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            resp = httpx.post(
                self._url,
                json={"model": self._model, "input": texts},
                headers=headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc

        items = sorted(payload["data"], key=lambda d: d["index"])
        vectors = [d["embedding"] for d in items]
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embedding count mismatch: {len(vectors)} != {len(texts)}"
            )
        for v in vectors:
            if len(v) != self._dimensions:
                raise EmbeddingError(
                    f"embedding dimension mismatch: got {len(v)}, "
                    f"expected {self._dimensions} (check EMBEDDING_DIMENSIONS)"
                )
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
