"""
Debug helper: run a retrieval query against the vector store from the CLI.

Usage:
    python -m rag.query "blink the blue LED using DMA"
    python -m rag.query "blue LED" --top-k 3
"""

import argparse
import logging


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Query the STM32 RAG store for debugging")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args(argv)

    # Import lazily so argparse errors stay fast.
    from config import RAG_TOP_K
    from rag.retriever import Retriever

    retriever = Retriever()
    result = retriever.retrieve(args.query, top_k=args.top_k or RAG_TOP_K)

    if not result.text:
        print("No clean context retrieved.")
        return 1

    print("sources:", ", ".join(result.sources))
    print("-" * 60)
    print(result.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
