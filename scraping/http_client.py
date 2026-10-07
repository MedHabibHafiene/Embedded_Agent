"""
Polite HTTP layer for the scraping package.

Site-friendly, cache-friendly rules enforced here:
  - robots.txt is fetched once per domain and respected
  - at most one request per second per domain
  - a descriptive User-Agent, no spoofing
  - every raw response is cached under scraping/raw/<domain>/<hash>.html, so
    re-running the scrape command skips pages it already downloaded unless
    --force is passed
"""

import hashlib
import logging
import time
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

USER_AGENT = "EmbeddedAgent-RAG/1.0 (personal research agent for local docs)"
REQUEST_TIMEOUT_SEC = 20
RATE_LIMIT_SEC = 1.0
MAX_RETRIES = 2
SITEMAP_DEPTH_LIMIT = 3

RAW_CACHE_DIR = Path(__file__).resolve().parent / "raw"

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_request_at: dict[str, float] = {}


def _cache_path(url: str) -> Path:
    domain = urlsplit(url).netloc.replace(":", "_")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return RAW_CACHE_DIR / domain / f"{digest}.html"


def _robots_for(url: str) -> urllib.robotparser.RobotFileParser | None:
    parts = urlsplit(url)
    domain = parts.netloc
    if domain not in _robots_cache:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(f"{parts.scheme}://{domain}/robots.txt")
        try:
            with urllib.request.urlopen(
                f"{parts.scheme}://{domain}/robots.txt", timeout=REQUEST_TIMEOUT_SEC
            ) as resp:
                parser.parse(resp.read().decode("utf-8", errors="replace").splitlines())
        except Exception:
            # Unreachable/unparseable robots.txt: no rules to enforce.
            _robots_cache[domain] = None
            return None
        _robots_cache[domain] = parser
    return _robots_cache[domain]


def _rate_limit(domain: str) -> None:
    wait = RATE_LIMIT_SEC - (time.monotonic() - _last_request_at.get(domain, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_request_at[domain] = time.monotonic()


def fetch(url: str, force: bool = False) -> str | None:
    """Fetch a URL as text (cached on disk). None on robots-block/failure."""
    cache = _cache_path(url)
    if cache.exists() and not force:
        return cache.read_text(encoding="utf-8", errors="replace")

    robots = _robots_for(url)
    if robots is not None and not robots.can_fetch(USER_AGENT, url):
        log.warning("robots.txt disallows %s - skipping", url)
        return None

    domain = urlsplit(url).netloc
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        _rate_limit(domain)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(body, encoding="utf-8")
            return body
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            if isinstance(e, TimeoutError):
                # A tarpit (st.com-style CDN) is not a transient error -
                # retrying just burns RATE_LIMIT-waiting minutes every run.
                break
    log.warning("Could not fetch %s: %s", url, last_error)
    return None


# --------------------------------------------------------------- sitemaps
def _parse_sitemap(xml: str) -> list[str]:
    import re

    return re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)


def sitemap_urls(
    sitemap_url: str, url_filter_pattern: str, max_urls: int, depth: int = 0
) -> list[str]:
    """Recursively walk a sitemap (or sitemap index) and return matching URLs.

    url_filter_pattern is applied only to FINAL document URLs; child
    sitemaps (.xml) are always followed up to SITEMAP_DEPTH_LIMIT.
    """
    if depth > SITEMAP_DEPTH_LIMIT:
        log.warning("Sitemap recursion limit reached at %s", sitemap_url)
        return []

    xml = fetch(sitemap_url)
    if xml is None:
        return []

    import re

    matched: list[str] = []
    for loc in _parse_sitemap(xml):
        if loc.endswith(".xml") or loc.endswith(".xml.gz"):
            if loc == sitemap_url:
                continue
            matched.extend(sitemap_urls(loc, url_filter_pattern, max_urls - len(matched), depth + 1))
        elif re.match(url_filter_pattern, loc):
            matched.append(loc)
        if len(matched) >= max_urls:
            log.info("Page cap (%d) reached for %s", max_urls, sitemap_url)
            break
    return matched[:max_urls]


def discover_sitemap_urls(
    root_url: str, url_filter_pattern: str, max_urls: int, sitemap_hints: tuple[str, ...] = ()
) -> list[str]:
    """Find a site's pages: robots.txt `Sitemap:` lines first, then hints.

    Returns the URLs from the first source that yields matches."""
    import re

    candidates: list[str] = list(sitemap_hints)
    parts = urlsplit(root_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    robots_text = fetch(robots_url)
    if robots_text:
        for line in robots_text.splitlines():
            match = re.match(r"\s*sitemap:\s*(\S+)", line, re.IGNORECASE)
            if match:
                candidates.append(match.group(1))

    for sitemap in dict.fromkeys(candidates):  # dedupe, keep order
        urls = sitemap_urls(sitemap, url_filter_pattern, max_urls)
        if urls:
            return urls

    log.warning("No sitemap pages discovered for %s", root_url)
    return []
