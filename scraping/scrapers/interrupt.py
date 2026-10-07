"""
Memfault's Interrupt blog: the "embedded systems in general" source -
firmware architecture, debugging, watchdogs, CI, memory, bootloaders.
Covers the general-embedded half of the agent's knowledge, complementing
the STM32-specific sources.
"""

from scraping.sources import Source

SOURCE = Source(
    name="interrupt",
    description="Embedded-systems engineering practice (Memfault Interrupt blog)",
    root_url="https://interrupt.memfault.com",
    sitemap_hints=("https://interrupt.memfault.com/sitemap.xml",),
    # Article pages only - excludes /blog/tags/... and non-blog URLs.
    url_filter=r"^https://interrupt\.memfault\.com/blog/[a-z0-9-]+/?$",
    content_selector=None,
    max_pages=60,
)
