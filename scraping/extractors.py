"""
HTML -> clean plain text extraction.

Generic enough for arbitrary blog/wiki themes: instead of trusting a
theme-specific CSS class, this strips known junk (scripts, nav menus,
sidebars, headers/footers, cookie banners, ...) and then picks the
container with the highest density of real content (<p> paragraphs and
<pre> code blocks). That works across WordPress, WPBakery, Jekyll, and
most other layouts without per-site selectors.
"""

import logging
import re

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

JUNK_TAGS = (
    "script", "style", "noscript", "iframe", "form", "svg", "button",
    "nav", "header", "footer", "aside",
)

# Class-token markers that identify non-content regions in most themes.
# Matched at TOKEN level (a token is junk if one of its -/_ separated
# components equals a marker), except for layout-flag prefixes.
JUNK_CLASS_MARKERS = (
    "menu", "nav", "sidebar", "widget", "footer", "header", "subheader",
    "cookie", "newsletter", "subscription", "comment", "breadcrumb",
    "social", "share", "related", "advert", "banner", "search", "modal",
)

# Layout flags that CONTAIN a marker word but describe the page layout,
# not the element itself: e.g. `for_sidebar` = "this section has a
# sidebar" (Impreza/WPBakery themes). Never junk.
_LAYOUT_FLAG_PREFIXES = ("for_", "has_", "with_", "no_", "at_", "is_")

# Structural roots are never decomposed, whatever their classes - the
# <body> of Impreza themes carries classes like `header_hor` describing
# the header LAYOUT, and killing the body kills the page.
_PROTECTED_TAGS = {"body", "html", "main", "article"}


def _class_is_junk(classes: list[str]) -> bool:
    for token in classes:
        lowered = token.lower()
        if lowered.startswith(_LAYOUT_FLAG_PREFIXES):
            continue
        components = re.split(r"[-_]", lowered)
        if any(component in JUNK_CLASS_MARKERS for component in components):
            return True
    return False


def _strip_junk(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(JUNK_TAGS):
        tag.decompose()
    # Collect first, decompose after: mutating while iterating is fragile.
    doomed = []
    for element in soup.find_all(True):
        if element.name in _PROTECTED_TAGS:
            continue
        attrs = getattr(element, "attrs", None)
        if not attrs:
            continue
        if _class_is_junk(attrs.get("class") or []):
            doomed.append(element)
    for element in doomed:
        element.decompose()


def _pick_content_root(soup: BeautifulSoup):
    """Score containers by content density; among the top scorers, prefer
    the smallest (innermost) so leftover layout text stays out."""
    candidates = []
    for element in soup.find_all(("div", "article", "main", "section")):
        score = 2 * len(element.find_all("pre")) + len(element.find_all("p"))
        if score > 0:
            candidates.append((score, len(element.find_all(True)), element))
    if not candidates:
        return soup.body or soup

    best_score = max(score for score, _, _ in candidates)
    near_best = [c for c in candidates if c[0] >= best_score * 0.9]
    near_best.sort(key=lambda c: c[1])  # smallest DOM subtree first
    return near_best[0][2]


def html_to_text(html: str, selector: str | None = None, min_chars: int = 400) -> str:
    """Extract readable text from an HTML page ('' if the page looks empty)."""
    soup = BeautifulSoup(html, "html.parser")
    _strip_junk(soup)

    if selector:
        root = soup.select_one(selector) or _pick_content_root(soup)
    else:
        root = _pick_content_root(soup)
    if root is None:
        return ""

    text = root.get_text("\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)

    # Collapse runs of blank lines.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) < min_chars:
        log.debug("Extraction too short (%d chars) - probably a nav/empty page", len(cleaned))
        return ""
    return cleaned
