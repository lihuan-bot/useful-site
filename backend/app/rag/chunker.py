"""Character-based text chunking with paragraph-aware boundaries."""

from __future__ import annotations


def chunk_text(text: str, *, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split text into overlapping chunks, preferring paragraph boundaries.

    Simple and dependency-free: walk the text in steps of
    ``chunk_size - chunk_overlap``, nudging each cut to the nearest
    paragraph break within a small window when one exists.
    """
    if chunk_size <= chunk_overlap:
        raise ValueError("chunk_size must be greater than chunk_overlap")
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    step = chunk_size - chunk_overlap
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            # Prefer a paragraph boundary near the cut.
            window = text[end - 200 : end]
            cut = window.rfind("\n\n")
            if cut == -1:
                cut = window.rfind("\n")
            if cut != -1:
                end = end - 200 + cut + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - chunk_overlap, start + 1)
    return chunks
