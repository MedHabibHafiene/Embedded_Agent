"""
ControllersTech: register- and HAL-level STM32 tutorials (UART, DMA, SPI,
timers, ADC, low-power, ...). Excellent hands-on reference for "code
anything" requests. 46 posts as of the sitemap used here.
"""

from scraping.sources import Source

SOURCE = Source(
    name="controllerstech",
    description="STM32 HAL/register tutorials (UART, DMA, SPI, timers, ADC)",
    root_url="https://controllerstech.com",
    sitemap_hints=("https://controllerstech.com/sitemap_index.xml",),
    # Single-segment article slugs, excluding the AVR/ATtiny posts the site
    # has shifted toward recently - this is an STM32 corpus.
    url_filter=r"^https://controllerstech\.com/(?!(?:avr-|attiny))[a-z0-9-]+/?$",
    content_selector=None,  # WPBakery theme - use the density heuristic
    max_pages=60,
)
