"""
MicroControllersLab: broad STM32 tutorial set (GPIO, UART, ADC, PWM, I2C,
interrupts, displays, sensors) - many bare-metal and HAL examples per
peripheral, useful for general "code anything" requests.
"""

from scraping.sources import Source

SOURCE = Source(
    name="microcontrollerslab",
    description="Broad STM32 peripheral tutorials and examples",
    root_url="https://microcontrollerslab.com",
    sitemap_hints=("https://microcontrollerslab.com/sitemap_index.xml",),
    # Single-segment article slugs only (posts, not category/tag archives).
    url_filter=r"^https://microcontrollerslab\.com/[a-z0-9-]+/?$",
    content_selector=None,
    max_pages=80,
)
