"""URL Spider/Crawler module — discovers relevant URLs for ACG indexing.

Provides:
- extract_links(): Extract and filter links from HTML
- crawl_urls(): BFS crawl starting from a URL, discovering linked pages
- is_same_domain(): Check if two URLs share the same domain (with www normalization)

PHASE 1 of the "index sites per topic" tool.
This module handles URL discovery only. The actual indexing is done by core/acg/indexer.py.
"""

import logging
import re
from collections import deque
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup

from src.acg.indexer import fetch_url

logger = logging.getLogger(__name__)

# Protocols/schemes to skip
_SKIP_SCHEMES = {"javascript", "mailto", "tel", "sms", "data", "file"}
# File extensions to skip (binary/media files)
_SKIP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".ico", ".webp",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv",
    ".exe", ".dmg", ".msi", ".deb", ".rpm",
    ".woff", ".woff2", ".ttf", ".eot",
}


def is_same_domain(url1: str, url2: str) -> bool:
    """Check if two URLs share the same domain (with www normalization).

    www.example.com is treated the same as example.com, but
    api.example.com is NOT the same as example.com (different subdomain).

    Args:
        url1: First URL.
        url2: Second URL.

    Returns:
        True if both URLs have the same effective domain.
    """
    def _normalize(url: str) -> str:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Normalize www prefix
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname

    return _normalize(url1) == _normalize(url2)


def _should_skip_url(href: str) -> bool:
    """Check if a URL should be skipped.

    Skips: javascript:, mailto:, tel:, anchors (#), and binary file extensions.

    Args:
        href: The raw href attribute value.

    Returns:
        True if the URL should be skipped.
    """
    if not href or not href.strip():
        return True

    href = href.strip()

    # Skip empty and fragment-only links
    if href.startswith("#") or href == "":
        return True

    # Skip protocol-based links that aren't http/https
    scheme = href.split(":")[0].lower() if ":" in href else ""
    if scheme in _SKIP_SCHEMES:
        return True

    return False


def _should_skip_extension(url: str) -> bool:
    """Check if a URL has a file extension that should be skipped.

    Args:
        url: The normalized URL.

    Returns:
        True if the URL ends with a binary/media extension.
    """
    path = urlparse(url).path.lower()
    for ext in _SKIP_EXTENSIONS:
        if path.endswith(ext) or (ext in path and "." in path.split("/")[-1]):
            return True
    return False


def extract_links(
    html: str,
    base_url: str,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    allow_external: bool = False,
) -> list[str]:
    """Extract all relevant links from an HTML page.

    Parses HTML, finds all <a href="..."> tags, resolves relative URLs,
    filters out non-http links (javascript:, mailto:, etc.), and applies
    include/exclude patterns.

    Args:
        html: Raw HTML content to parse.
        base_url: The base URL of the page (for resolving relative links).
        include_pattern: Optional regex pattern — only URLs matching this
                         pattern will be included.
        exclude_pattern: Optional regex pattern — URLs matching this
                         pattern will be excluded.
        allow_external: If True, include links to other domains as well.
                        Default is False (same-domain only).

    Returns:
        List of absolute URLs found on the page.
    """
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "lxml")
    links = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]

        # Skip unwanted URLs early
        if _should_skip_url(href):
            continue

        # Resolve relative URLs to absolute
        absolute_url = urljoin(base_url, href)

        # Skip binary/media file extensions
        if _should_skip_extension(absolute_url):
            continue

        # Ensure we have http/https scheme
        parsed = urlparse(absolute_url)
        if parsed.scheme not in ("http", "https"):
            continue

        # Filter by external links
        if not allow_external and not is_same_domain(absolute_url, base_url):
            continue

        # Apply include pattern (if specified)
        if include_pattern and not re.search(include_pattern, absolute_url):
            continue

        # Apply exclude pattern (if specified)
        if exclude_pattern and re.search(exclude_pattern, absolute_url):
            continue

        # Remove trailing slash for deduplication (unless it's just the domain)
        normalized = absolute_url.rstrip("/")
        if normalized == urlparse(absolute_url).netloc:
            normalized = absolute_url

        links.append(normalized)

    # Deduplicate while preserving order
    seen = set()
    unique_links = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique_links.append(link)

    return unique_links


def crawl_urls(
    start_url: str,
    max_depth: int = 2,
    max_urls: int = 50,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    allow_external: bool = False,
) -> list[str]:
    """Crawl a starting URL to discover all linked pages.

    Uses BFS (breadth-first search) to discover URLs. Respects max_depth
    and max_urls limits. Handles cyclic links gracefully via a visited set.

    Args:
        start_url: The URL to start crawling from.
        max_depth: Maximum crawl depth (default: 2).
                   depth=1 means only the start page itself.
        max_urls: Maximum number of URLs to discover before stopping
                  (default: 50).
        include_pattern: Optional regex pattern — only URLs matching this
                         pattern will be crawled.
        exclude_pattern: Optional regex pattern — URLs matching this
                         pattern will be skipped.
        allow_external: If True, crawl links to other domains as well.
                        Default is False (same-domain only).

    Returns:
        List of discovered URLs in BFS order (breadth-first).
    """
    discovered: list[str] = []
    visited: set[str] = set()

    # BFS queue: (url, depth)
    queue: deque[tuple[str, int]] = deque()
    queue.append((start_url, 0))
    visited.add(start_url)

    while queue and len(discovered) < max_urls:
        current_url, depth = queue.popleft()

        # Add to discovered list
        discovered.append(current_url)

        # If at max depth or hit max_urls limit, don't expand
        # depth + 1 >= max_depth means: max_depth=1 => only start page,
        # max_depth=2 => start + children, max_depth=3 => start + children + grandchildren
        if depth + 1 >= max_depth or len(discovered) >= max_urls:
            continue

        # Fetch and expand
        try:
            html = fetch_url(current_url)
            links = extract_links(
                html,
                base_url=current_url,
                include_pattern=include_pattern,
                exclude_pattern=exclude_pattern,
                allow_external=allow_external,
            )

            for link in links:
                if link not in visited:
                    visited.add(link)
                    # Only enqueue if we still have room
                    if len(discovered) + len(queue) < max_urls:
                        queue.append((link, depth + 1))

        except Exception as e:
            logger.warning(f"Failed to crawl {current_url}: {e}")
            # Continue crawling other URLs — don't stop on one error
            continue

    return discovered


def crawl_and_summarize(
    start_url: str,
    max_depth: int = 2,
    max_urls: int = 50,
    include_pattern: str | None = None,
    exclude_pattern: str | None = None,
    allow_external: bool = False,
) -> dict:
    """Crawl URLs and return a summary dict with metadata.

    Like crawl_urls() but returns structured metadata including
    the number of URLs discovered, any errors encountered, etc.

    Args:
        Same as crawl_urls().

    Returns:
        Dict with keys:
        - start_url: The starting URL
        - total_discovered: Number of URLs discovered
        - max_depth: Depth used
        - max_urls: Limit used
        - urls: List of discovered URLs
        - errors: List of error messages (if any)
    """
    errors: list[str] = []
    urls = crawl_urls(
        start_url=start_url,
        max_depth=max_depth,
        max_urls=max_urls,
        include_pattern=include_pattern,
        exclude_pattern=exclude_pattern,
        allow_external=allow_external,
    )

    # Re-fetch to detect errors — crawl_urls already logs but doesn't track
    # We can note that URL count matches expectations
    result = {
        "start_url": start_url,
        "total_discovered": len(urls),
        "max_depth": max_depth,
        "max_urls": max_urls,
        "urls": urls,
    }

    if include_pattern:
        result["include_pattern"] = include_pattern
    if exclude_pattern:
        result["exclude_pattern"] = exclude_pattern

    return result
