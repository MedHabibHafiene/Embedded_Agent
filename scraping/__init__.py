"""
One-time, on-demand web scraping for the RAG store.

This package never runs automatically - not at server startup, not at import.
The single entry point is an explicit command:

    python -m scraping.run              # scrape all sources, then rebuild the index
    python -m scraping.run --list       # show registered sources
    python -m scraping.run --force      # re-download even if cached
    python -m scraping.run --no-ingest # scrape only, don't touch the index

Scraped pages are cached as raw HTML under scraping/raw/ and extracted to
plain-text files under scraping/output/<source>/, which rag.ingest indexes
alongside the PDFs in documentation/.
"""

from scraping.sources import Source

__all__ = ["Source"]
