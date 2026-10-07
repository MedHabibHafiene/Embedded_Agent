"""
Document loaders: turn files under documentation/ into raw text documents.

Supported formats:
  - .txt / .md  - read directly (UTF-8, undecodable bytes replaced)
  - .pdf        - extracted with pypdf, if installed (optional dependency)

Adding a new format means adding one `_read_*` function and registering it in
_LOADERS - nothing else in the pipeline needs to change.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".txt", ".md", ".pdf")


@dataclass
class LoadedDocument:
    """A single document ready for chunking.

    source: stable human-readable identifier (relative file path) - stored as
            chunk metadata so retrieval results can cite their origin.
    text:   full extracted plain text.
    """

    source: str
    text: str


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf_file(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning(
            "pypdf is not installed - skipping PDF %s (pip install pypdf)", path.name
        )
        return ""

    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        text = "\n".join(pages).strip()
        if not text:
            log.warning("PDF %s yielded no extractable text (scanned images?)", path.name)
        return text
    except Exception as e:  # a corrupt PDF must not abort the whole ingest
        log.warning("Failed to parse PDF %s: %s", path.name, e)
        return ""


_LOADERS = {
    ".txt": _read_text_file,
    ".md": _read_text_file,
    ".pdf": _read_pdf_file,
}


def load_file(path: Path, root: Path | None = None) -> LoadedDocument | None:
    """Load one file into a LoadedDocument, or None if unsupported/empty."""
    loader = _LOADERS.get(path.suffix.lower())
    if loader is None:
        return None

    text = loader(path)
    if not text.strip():
        return None

    source = path.relative_to(root).as_posix() if root else path.name
    return LoadedDocument(source=source, text=text)


def load_directory(docs_dir: Path) -> list[LoadedDocument]:
    """Load every supported document under docs_dir (recursively)."""
    if not docs_dir.exists():
        log.warning("Documentation directory %s does not exist", docs_dir)
        return []

    documents = []
    for path in sorted(docs_dir.rglob("*")):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        log.info("Loading %s", path.name)
        document = load_file(path, root=docs_dir)
        if document is not None:
            documents.append(document)

    if not documents:
        log.warning(
            "No loadable documents found in %s (supported: %s)",
            docs_dir,
            ", ".join(SUPPORTED_SUFFIXES),
        )
    return documents
