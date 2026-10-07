"""
STMicroelectronics official site (www.st.com).

NOTE (2026-09-19): st.com currently times out ALL non-interactive clients
from this machine - robots.txt and even direct /resource/en/... document
PDFs stall for 30+ seconds with both plain and browser user-agents (the CDN
tarpits non-browser traffic). The source is registered and ready, but
`python -m scraping.run --sources st_official` will yield nothing until the
site accepts the fetcher (different network, or ST relaxes the filter).

Practical alternative that works today: download ST's official PDFs (user
manuals, application notes, reference manuals from the product page) into
documentation/ and run `python -m rag.ingest` - the existing
"um stm32f407vg.pdf" arrived exactly that way.

Sitemaps: /sitemap.xml does not exist (404). The real ones are listed in
robots.txt. Their <loc> entries use the internal /content/st_com/<lang>/...
paths rather than the public /<lang>/... ones, and mix ~15 locales.
"""

from scraping.sources import Source

SOURCE = Source(
    name="st_official",
    description="Official STMicroelectronics STM32 product, software and application-note pages",
    root_url="https://www.st.com",
    sitemap_hints=(
        "https://www.st.com/content/st_com/sitemap-index.xml",
        "https://www.st.com/content/st_com/product-tabs-en.xml",
    ),
    # English pages only - st.com serves every page in ~15 locales, which
    # would multiply the corpus with identical translated content.
    # Accepts both public (/en/...) and sitemap (/content/st_com/en/...) forms.
    url_filter=(
        r"(?i)^https://www\.st\.com/(?:content/st_com/)?en/"
        r"(?:embedded-software|microcontrollers-microprocessors|"
        r"application-notes|evaluation-tools|knowledge-articles)"
        r"/[a-z0-9-]+(?:/[a-z0-9._-]+)*(?:\.html)?/?$"
    ),
    content_selector=None,
    max_pages=100,
)