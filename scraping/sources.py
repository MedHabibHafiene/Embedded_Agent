"""
Source registry model.

A Source describes everything needed to scrape one site: where to find its
page list (sitemap hints + URL filter), and how to extract article text.
Adding a new site = one small module in scraping/scrapers/ that defines a
Source and registers it in REGISTRY - nothing else changes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str                     # short id -> output folder & citation prefix
    description: str
    root_url: str                 # site root (for robots.txt sitemap discovery)
    url_filter: str               # regex applied to discovered page URLs
    sitemap_hints: tuple[str, ...] = ()  # known sitemap/index URLs to try
    content_selector: str | None = None  # optional CSS selector; None = auto
    max_pages: int = 50           # hard cap per run - keep the index curated
