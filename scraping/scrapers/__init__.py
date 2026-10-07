"""Scraper modules - one per site. Each defines a Source and is registered below."""

from scraping.scrapers import controllerstech, interrupt, microcontrollerslab, st_official

# Order matters only for log output. Add a new site by creating a module
# here with a Source and appending it to this list.
REGISTRY = [
    controllerstech.SOURCE,
    microcontrollerslab.SOURCE,
    interrupt.SOURCE,
    st_official.SOURCE,
]

__all__ = ["REGISTRY"]
