"""
RAG layer - retrieval-augmented generation for STM32 hardware knowledge.

Data flow:

    documentation/  ──loaders──▶  raw text  ──chunking──▶  chunks
    rag/seed_docs.py (canonical hardware facts)
                                    │
                                    ▼ ingest (python -m rag.ingest)
                          rag.vector_store.VectorStore (ChromaDB)
                                    │
                                    ▼
                          rag.retriever.Retriever (query -> clean context)
                                    │
                                    ▼
                          agent.orchestrator (prompt building)

Public API:
    from rag import Retriever, VectorStore, CORE_HARDWARE_FACTS
"""

from rag.seed_docs import CORE_HARDWARE_FACTS
from rag.vector_store import RetrievedChunk, VectorStore
from rag.retriever import Retriever, RetrievalResult

__all__ = [
    "CORE_HARDWARE_FACTS",
    "Retriever",
    "RetrievalResult",
    "RetrievedChunk",
    "VectorStore",
]
