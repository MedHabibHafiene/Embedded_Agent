"""
The scrape command - the ONLY way scraping ever runs.

    python -m scraping.run                    scrape all sources + rebuild index
    python -m scraping.run --list             show registered sources
    python -m scraping.run --sources controllerstech,interrupt
    python -m scraping.run --force            re-download even cached pages
    python -m scraping.run --no-ingest        scrape only, skip re-indexing

One-time by design: raw HTML is cached under scraping/raw/, extracted text
under scraping/output/ - re-running skips what it already has and never runs
at server startup or project import.
"""

import argparse
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from scraping.extractors import html_to_text
from scraping.http_client import discover_sitemap_urls, fetch
from scraping.scrapers import REGISTRY

log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


@dataclass
class ScrapeReport:
    source: str
    discovered: int
    saved: int
    skipped_unreachable: int
    skipped_low_content: int
    reused_cache: int


def _slug_for(url: str) -> str:
    """Filesystem-safe, stable filename for a page URL."""
    from urllib.parse import urlsplit

    path = urlsplit(url).path.strip("/").replace("/", "-") or "index"
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", path).strip("-")[:80].lower()
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}.txt"


def scrape_source(source, force: bool = False) -> ScrapeReport:
    urls = discover_sitemap_urls(
        source.root_url, source.url_filter, source.max_pages, source.sitemap_hints
    )
    report = ScrapeReport(source.name, len(urls), 0, 0, 0, 0)
    if not urls:
        return report

    out_dir = OUTPUT_DIR / source.name
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("[%s] %d page(s) to scrape", source.name, len(urls))
    for url in urls:
        out_file = out_dir / _slug_for(url)
        if out_file.exists() and not force:
            report.reused_cache += 1
            continue

        html = fetch(url, force=force)
        if html is None:
            report.skipped_unreachable += 1
            continue

        text = html_to_text(html, selector=source.content_selector)
        if not text:
            report.skipped_low_content += 1
            continue

        out_file.write_text(text, encoding="utf-8")
        report.saved += 1

    log.info(
        "[%s] saved=%d reused=%d unreachable=%d low-content=%d",
        source.name, report.saved, report.reused_cache,
        report.skipped_unreachable, report.skipped_low_content,
    )
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--list", action="store_true", help="list registered sources and exit")
    parser.add_argument("--sources", type=str, help="comma-separated source names to scrape")
    parser.add_argument("--force", action="store_true", help="re-download even cached pages")
    parser.add_argument("--no-ingest", action="store_true", help="don't rebuild the RAG index")
    parser.add_argument("--max-pages", type=int, help="override the per-source page cap")
    args = parser.parse_args(argv)

    if args.list:
        for source in REGISTRY:
            print(f"  {source.name:20s} {source.description} (cap: {source.max_pages})")
        return 0

    selected = REGISTRY
    if args.sources:
        names = {n.strip() for n in args.sources.split(",")}
        selected = [s for s in REGISTRY if s.name in names]
        unknown = names - {s.name for s in selected}
        if unknown:
            parser.error(f"unknown source(s): {', '.join(sorted(unknown))}")

    total_saved = 0
    reports = []
    for source in selected:
        if args.max_pages:
            source = type(source)(**{**source.__dict__, "max_pages": args.max_pages})
        report = scrape_source(source, force=args.force)
        reports.append(report)
        total_saved += report.saved
        print(
            f"{report.source}: {report.saved} new page(s) saved, "
            f"{report.reused_cache} already scraped, "
            f"{report.skipped_unreachable} unreachable, "
            f"{report.skipped_low_content} skipped (no content)"
        )

    # ---- end-of-run summary: how much this project has scraped ----
    name_width = max(len(r.source) for r in reports) + 2
    grand_total = 0
    sites_with_content = 0
    lines = []
    for report in reports:
        pages = report.saved + report.reused_cache
        grand_total += pages
        sites_with_content += pages > 0
        lines.append(
            f"  {report.source:<{name_width}} {pages:>4} page(s) scraped "
            f"({report.saved} new this run)"
        )
    print("\nScrape summary - pages in the corpus per website:")
    print("\n".join(lines))
    print("  " + "-" * (name_width + 40))
    print(
        f"  TOTAL: {grand_total} page(s) scraped from "
        f"{sites_with_content} website(s), {total_saved} new this run."
    )

    if args.no_ingest:
        print("Index NOT rebuilt (--no-ingest). Run `python -m rag.ingest` when ready.")
        return 0

    from rag.ingest import ingest

    try:
        summary = ingest()
    except RuntimeError as e:
        print(f"\nScraped {total_saved} new page(s), but the index rebuild failed:\n{e}")
        return 1
    print(
        f"Index rebuilt: {summary.total_indexed} chunk(s) "
        f"({summary.seed_chunks} seed facts + {summary.document_chunks} from documents)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
