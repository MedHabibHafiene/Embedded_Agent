"""
Vector store: a thin, testable wrapper around ChromaDB persistence.

Responsibilities (and nothing else):
  - own the PersistentClient and the named collection
  - add / clear / count documents
  - nearest-neighbour query -> list[RetrievedChunk]
  - persist lightweight metadata (seed/corpus fingerprints) next to the DB

Embeddings use ChromaDB's DefaultEmbeddingFunction (all-MiniLM-L6-v2), the
same model the database on disk was already built with.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

from config import (
    CHROMA_API_KEY,
    CHROMA_DATABASE,
    CHROMA_TENANT,
    PROJECT_ROOT,
    RAG_BACKEND,
    RAG_COLLECTION,
    RAG_DB_DIR,
)

log = logging.getLogger(__name__)

# chromadb 0.5.3 calls the legacy posthog 3-arg capture(); against the posthog
# version installed here that raises TypeError before posthog's disabled-flag
# is ever checked, so chromadb logs a benign ERROR on every client start.
# Mute that specific logger - telemetry is disabled below anyway.
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

META_FILENAME = "rag_meta.json"


@dataclass
class RetrievedChunk:
    """One retrieved context chunk with its citation metadata."""

    text: str
    source: str
    distance: float


def content_fingerprint(texts: list[str]) -> str:
    """Stable SHA-256 over a list of texts - detects stale persisted stores."""
    joined = "\n".join(texts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _batches(seq: list, size: int):
    """Yield seq in consecutive slices of at most `size` items."""
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


# ChromaDB refuses single adds larger than this (5461 on 0.5.3); stay well
# below it and keep embedding memory flat.
MAX_ADD_BATCH = 1000


class VectorStore:
    """Vector store over either Chroma Cloud or a local persistent DB.

    The backend is chosen by config.RAG_BACKEND ("cloud" | "local"); both
    expose the identical Chroma collection API, so the rest of the system
    never knows which one is in use. Fingerprint bookkeeping (rag_meta.json)
    always lives on the local disk - the cloud has no filesystem to keep it.
    """

    def __init__(
        self,
        persist_dir: Path = RAG_DB_DIR,
        collection_name: str = RAG_COLLECTION,
        backend: str | None = None,
    ):
        self.backend = (backend or RAG_BACKEND).lower()
        self.embedding_function = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self._connect(collection_name, persist_dir)

    def _connect(self, collection_name: str, persist_dir: Path):
        if self.backend == "cloud":
            if not (CHROMA_API_KEY and CHROMA_TENANT and CHROMA_DATABASE):
                raise RuntimeError(
                    "RAG_BACKEND=cloud requires CHROMA_API_KEY, CHROMA_TENANT "
                    "and CHROMA_DATABASE in the environment (see .env)."
                )
            log.info(
                "Connecting to Chroma Cloud (tenant/database: %s/%s)...",
                CHROMA_TENANT, CHROMA_DATABASE,
            )
            self.client = chromadb.CloudClient(
                api_key=CHROMA_API_KEY, tenant=CHROMA_TENANT, database=CHROMA_DATABASE
            )
            self._meta_dir = PROJECT_ROOT / "data"
            self._meta_dir.mkdir(parents=True, exist_ok=True)
            return self.client.get_or_create_collection(
                name=collection_name, embedding_function=self.embedding_function
            )

        # Local persistent backend.
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        # chromadb 0.5.3 ships a telemetry client that crashes against the
        # posthog version installed here and spams ERROR lines on every
        # startup. Anonymized telemetry is off; the vector store is fully
        # functional. (The module-level logger mute below covers CloudClient,
        # which does not accept settings.)
        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=chromadb.config.Settings(anonymized_telemetry=False),
        )
        self._meta_dir = self.persist_dir
        return self.client.get_or_create_collection(
            name=collection_name, embedding_function=self.embedding_function
        )

    # ---------------------------------------------------------- CRUD
    def count(self) -> int:
        return self.collection.count()

    def add(self, documents: list[str], ids: list[str], metadatas: list[dict]) -> None:
        if not documents:
            return
        # ChromaDB caps single adds (5461 items on 0.5.3) - a full corpus
        # rebuild easily exceeds it, so insert in bounded batches.
        for doc_batch, id_batch, meta_batch in zip(
            _batches(documents, MAX_ADD_BATCH),
            _batches(ids, MAX_ADD_BATCH),
            _batches(metadatas, MAX_ADD_BATCH),
        ):
            self.collection.add(documents=doc_batch, ids=id_batch, metadatas=meta_batch)

    def clear(self) -> None:
        existing = self.collection.get()["ids"]
        if existing:
            self.collection.delete(ids=existing)

    def query(self, text: str, top_k: int, where: dict | None = None) -> list[RetrievedChunk]:
        results = self.collection.query(
            query_texts=[text], n_results=top_k, where=where
        )
        chunks = []
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results.get("distances") or [[]]
        for i, doc in enumerate(documents):
            chunks.append(
                RetrievedChunk(
                    text=doc,
                    source=(metadatas[i] or {}).get("source", "unknown"),
                    distance=(distances[0][i] if distances and i < len(distances[0]) else -1.0),
                )
            )
        return chunks

    def fetch_by_source(self, source: str) -> list[RetrievedChunk]:
        """Fetch ALL documents with a given source - exact metadata filter,
        not similarity search. ChromaDB applies `where` on queries only to
        the top-N nearest neighbors of the entire index, so a filtered
        similarity query cannot be used to fetch every seed fact."""
        result = self.collection.get(
            where={"source": source}, include=["documents", "metadatas"]
        )
        return [
            RetrievedChunk(text=doc, source=source, distance=-1.0)
            for doc in result["documents"]
        ]

    # ---------------------------------------------------------- metadata
    def _meta_path(self) -> Path:
        return self._meta_dir / META_FILENAME

    def read_meta(self) -> dict:
        path = self._meta_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def write_meta(self, meta: dict) -> None:
        self._meta_path().write_text(json.dumps(meta, indent=2), encoding="utf-8")
