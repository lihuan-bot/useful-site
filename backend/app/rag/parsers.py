"""Text extraction from uploaded documents (.pdf / .docx / .txt)."""

from __future__ import annotations

import io

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}


class ParseError(Exception):
    """Raised when a document cannot be parsed into text."""


def parse_document(filename: str, data: bytes) -> str:
    """Extract plain text based on the file extension."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return _parse_pdf(data)
    if lower.endswith(".docx"):
        return _parse_docx(data)
    if lower.endswith(".txt"):
        return _parse_txt(data)
    raise ParseError(f"unsupported file type: {filename}")


def _parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n\n".join(pages).strip()
    if not text:
        raise ParseError("no extractable text in PDF (scanned image?)")
    return text


def _parse_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
    if not text.strip():
        raise ParseError("no extractable text in docx")
    return text


def _parse_txt(data: bytes) -> str:
    # UTF-8 first; fall back to common Chinese encodings.
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            text = data.decode(encoding)
            if text.strip():
                return text
        except (UnicodeDecodeError, ValueError):
            continue
    raise ParseError("unable to decode text file")
