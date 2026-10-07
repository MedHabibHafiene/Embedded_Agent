"""
Rebuild the vector index from documentation/ plus the core hardware facts.

Usage:
    python -m rag.ingest              # rebuild from the configured DOCS_DIR
    python -m rag.ingest --docs path/to/docs
    python -m rag.ingest --summary    # show what is indexed without rebuilding

This is the ONLY command that clears the collection; server startup only
seeds core facts into an empty store and never deletes anything.
"""

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from config import DOCS_DIR, RAG_CHUNK_MAX_CHARS, SCRAPED_DIR
from rag.chunking import chunk_document, chunk_text
from rag.loaders import load_directory
from rag.seed_docs import CORE_HARDWARE_FACTS
from rag.retriever import SEED_SOURCE
from rag.vector_store import VectorStore, content_fingerprint

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
    seed_chunks: int
    document_files: int
    document_chunks: int
    total_indexed: int


def _restore_seed_facts(store: VectorStore) -> None:
    """After a failed rebuild, put the core facts back so the agent keeps
    working (retrieval without them degrades badly)."""
    try:
        store.add(
            documents=list(CORE_HARDWARE_FACTS),
            ids=[f"seed-{i}" for i in range(len(CORE_HARDWARE_FACTS))],
            metadatas=[{"source": SEED_SOURCE}] * len(CORE_HARDWARE_FACTS),
        )
        log.warning("Restored %d core seed facts after the failed rebuild.",
                    len(CORE_HARDWARE_FACTS))
    except Exception as e:
        log.warning("Could not restore seed facts: %s", e)


def _id_prefix(source: str) -> str:
    """File-system-safe, stable id prefix derived from the source path."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", source).strip("-").lower() or "doc"
    digest = content_fingerprint([source])[:8]
    return f"{slug}-{digest}"


def ingest(docs_dir: Path = DOCS_DIR, store: VectorStore | None = None) -> IngestReport:
    store = store or VectorStore()
    documents = load_directory(docs_dir)
    # Scraped pages (scraping/output/) are indexed alongside the PDFs.
    if SCRAPED_DIR.exists():
        documents.extend(load_directory(SCRAPED_DIR))

    all_texts: list[str] = []
    all_ids: list[str] = []
    all_meta: list[dict] = []

    # 1. Core hardware facts - always first, with stable ids.
    seed_ids = [f"seed-{i}" for i in range(len(CORE_HARDWARE_FACTS))]
    all_texts.extend(CORE_HARDWARE_FACTS)
    all_ids.extend(seed_ids)
    all_meta.extend([{"source": SEED_SOURCE}] * len(CORE_HARDWARE_FACTS))

    # 2. Documentation files - chunked, each chunk cites its origin file.
    for document in documents:
        for i, chunk in enumerate(chunk_document(document, RAG_CHUNK_MAX_CHARS)):
            all_texts.append(chunk)
            all_ids.append(f"{_id_prefix(document.source)}-{i}")
            all_meta.append({"source": document.source})

    log.info("Rebuilding index: clearing %d existing document(s).", store.count())
    store.clear()
    try:
        store.add(documents=all_texts, ids=all_ids, metadatas=all_meta)
    except Exception as e:
        if "quota" in str(e).lower():
            # A quota-rejected rebuild must not leave an empty store - the
            # retriever would fall back to near-zero context.
            _restore_seed_facts(store)
            raise RuntimeError(
                f"Chroma Cloud rejected the ingest: the record quota was exceeded "
                f"(the tier caps this database at ~300 records; the corpus is "
                f"{len(all_texts)} chunks). Either request a quota increase at "
                "https://trychroma.com/request-quota-increase, or switch to the "
                "local backend by setting RAG_BACKEND=local in .env and re-running "
                "this command. The core seed facts have been restored meanwhile."
            ) from e
        raise
    store.write_meta(
        {
            "seed_fingerprint": content_fingerprint(list(CORE_HARDWARE_FACTS)),
            "corpus_fingerprint": content_fingerprint(all_texts),
            "indexed_files": len(documents),
        }
    )

    report = IngestReport(
        seed_chunks=len(CORE_HARDWARE_FACTS),
        document_files=len(documents),
        document_chunks=len(all_texts) - len(CORE_HARDWARE_FACTS),
        total_indexed=store.count(),
    )
    log.info(
        "Ingest complete: %d seed facts + %d chunks from %d file(s) = %d indexed.",
        report.seed_chunks,
        report.document_chunks,
        report.document_files,
        report.total_indexed,
    )
    return report


def summarize(store: VectorStore | None = None) -> dict:
    """Describe the current index without touching it."""
    store = store or VectorStore()
    sample = store.collection.get(include=["metadatas"])
    sources = {}
    for meta in sample.get("metadatas") or []:
        source = (meta or {}).get("source", "unknown")
        sources[source] = sources.get(source, 0) + 1
    return {"total_documents": store.count(), "by_source": sources, "meta": store.read_meta()}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs", type=Path, default=DOCS_DIR, help="directory to scan for documents")
    parser.add_argument("--summary", action="store_true", help="show the current index contents and exit")
    args = parser.parse_args(argv)

    if args.summary:
        for key, value in summarize().items():
            print(f"{key}: {value}")
        return 0

    try:
        report = ingest(docs_dir=args.docs)
    except RuntimeError as e:
        print(f"Index rebuild failed:\n{e}")
        return 1
    print(
        f"Indexed {report.total_indexed} chunk(s): "
        f"{report.seed_chunks} seed facts + {report.document_chunks} chunks "
        f"from {report.document_files} documentation file(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
