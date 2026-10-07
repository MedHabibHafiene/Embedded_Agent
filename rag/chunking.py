"""
Text chunking: split extracted document text into embedding-sized chunks.

Strategy: split on blank lines (paragraph boundaries), then pack consecutive
paragraphs into chunks of at most max_chars characters; an oversized paragraph
is hard-split at the limit. Blank-line separation is preserved inside a chunk
so the embedded text keeps its structure.
"""

from rag.loaders import LoadedDocument


def chunk_text(text: str, max_chars: int = 1000) -> list[str]:
    """Split plain text into chunks of at most max_chars characters."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: list[str] = []
    current = ""

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
        current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            # Hard-split an oversized paragraph at the size limit.
            for start in range(0, len(paragraph), max_chars):
                piece = paragraph[start : start + max_chars].strip()
                if piece:
                    chunks.append(piece)
            continue

        if current and len(current) + 2 + len(paragraph) > max_chars:
            flush()
        current = f"{current}\n\n{paragraph}" if current else paragraph

    flush()
    return chunks


def chunk_document(document: LoadedDocument, max_chars: int = 1000) -> list[str]:
    """Chunk a single LoadedDocument (kept for explicitness at call sites)."""
    return chunk_text(document.text, max_chars=max_chars)
