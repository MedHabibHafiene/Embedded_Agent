"""
Retriever: turn a natural-language request into clean hardware context.

Wraps a VectorStore with the two pieces of retrieval policy this project needs:
  - auto-seed the core hardware facts into an empty database (so a fresh
    checkout works with zero manual steps)
  - drop garbled/mangled chunks before they reach the LLM prompt
"""

import logging
from dataclasses import dataclass, field

from config import RAG_TOP_K
from rag.seed_docs import CORE_HARDWARE_FACTS
from rag.vector_store import VectorStore, content_fingerprint

log = logging.getLogger(__name__)

SEED_SOURCE = "core-hardware-facts"


@dataclass
class RetrievalResult:
    """Cleaned retrieval output ready for prompt building."""

    text: str
    sources: list[str] = field(default_factory=list)
    distances: list[float] = field(default_factory=list)


def _looks_garbled(text: str, threshold: float = 0.4) -> bool:
    """Heuristic: real prose doesn't have a huge fraction of 1-character
    'words'. Catches mis-decoded / mangled text before it reaches the LLM."""
    tokens = [t for t in text.split(" ") if t]
    if len(tokens) < 6:
        return False
    single_char_ratio = sum(1 for t in tokens if len(t) == 1) / len(tokens)
    return single_char_ratio > threshold


class Retriever:
    def __init__(self, vector_store: VectorStore | None = None, top_k: int = RAG_TOP_K):
        self.store = vector_store or VectorStore()
        self.top_k = top_k
        self.ensure_seeded()

    def ensure_seeded(self) -> None:
        """Seed core hardware facts only when the store is empty.

        An explicit `python -m rag.ingest` (re)builds the full index including
        documentation/; startup never wipes existing data.
        """
        if self.store.count() > 0:
            return

        log.info("Empty vector store - seeding %d core hardware facts.", len(CORE_HARDWARE_FACTS))
        self.store.add(
            documents=list(CORE_HARDWARE_FACTS),
            ids=[f"seed-{i}" for i in range(len(CORE_HARDWARE_FACTS))],
            metadatas=[{"source": SEED_SOURCE}] * len(CORE_HARDWARE_FACTS),
        )
        self.store.write_meta(
            {"seed_fingerprint": content_fingerprint(list(CORE_HARDWARE_FACTS))}
        )

    def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        """Query the store and return only clean, useful chunks.

        Two-stage retrieval: ALL core hardware facts lead the context (they
        are few and tiny, and they encode the board-specific constraints
        generation depends on - never risk a ranking burying one), then
        documentation chunks supplement them. Without this, a large indexed
        manual outranks the small seed set with generic noise.
        """
        top_k = top_k or self.top_k

        # All core facts, always - fetched by exact source filter (a
        # similarity query cannot reliably return them all).
        seed_chunks = self.store.fetch_by_source(SEED_SOURCE)
        doc_chunks = self.store.query(query, top_k=top_k)

        clean: list = []
        seen: set[str] = set()
        for chunk in seed_chunks + doc_chunks:
            if chunk.text in seen:
                continue
            if _looks_garbled(chunk.text):
                log.warning("Discarding garbled retrieved chunk: %r...", chunk.text[:60])
                continue
            seen.add(chunk.text)
            clean.append(chunk)

        if not clean:
            log.warning(
                "All retrieved context looked garbled/corrupted; proceeding with "
                "no hardware context. Consider running: python -m rag.ingest"
            )
            return RetrievalResult(text="")

        log.info(
            "Retrieved %d context chunk(s) (%d core fact(s), %d from docs) from: %s",
            len(clean),
            len([c for c in clean if c.source == SEED_SOURCE]),
            len([c for c in clean if c.source != SEED_SOURCE]),
            ", ".join(sorted({c.source for c in clean})),
        )
        return RetrievalResult(
            text="\n".join(c.text for c in clean),
            sources=sorted({c.source for c in clean}),
            distances=[c.distance for c in clean],
        )
