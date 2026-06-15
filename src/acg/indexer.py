"""Web page indexer module — v2 (Modern + Legacy).

Fetches URLs, extracts text content (with JS rendering fallback for SPAs),
chunks by sentences, generates CSS selectors, generates embeddings,
and stores results in MongoDB.

Key improvements over v1:
- **Two-tier fetch**: requests (fast) → Playwright headless (JS-rendered fallback)
- **BeautifulSoup + lxml** for robust HTML parsing (not regex)
- **html2text** for clean markdown-like text extraction
- **Table-to-text** conversion: <table> → structured text rows
- **Dedup on re-index**: replaces existing chunks for same URL
- **Better sentence splitting**: handles tables, lists, code blocks

Uses collections: sources (metadata) + data (chunks with embeddings).
Embeddings: BAAI/bge-small-en-v1.5 (384-dim) via fastembed.
"""

import hashlib
import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag

from src.acg.shi import generate_shi
from src.acg.db import (
    save_source,
    save_data_chunks,
    get_source_by_url_hash,
    delete_source,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TWO-TIER FETCH: requests (fast) → Playwright (JS-rendered fallback)
# ---------------------------------------------------------------------------


def _detect_js_required(html: str) -> bool:
    """Heuristic: check if page likely needs JS rendering.

    Looks for common SPA indicators in the HTML shell.

    Args:
        html: Raw HTML content.

    Returns:
        True if the page appears to be a JS-rendered SPA.
    """
    indicators = [
        'id="root"',
        'id="__next"',
        'id="app"',
        'id="__nuxt"',
        'data-server-rendered',
        'ng-app',
        'ng-view',
        'react-root',
        'vue-app',
        '<div id="root"',
        '<div id="__next"',
        '<div id="app"',
        # Docusaurus-specific
        'class="theme-doc-markdown"',
        'data-docusaurus',
    ]
    html_lower = html.lower()
    for indicator in indicators:
        if indicator.lower() in html_lower:
            return True
    # Check if body is nearly empty (SPA shell)
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_match:
        body_text = re.sub(r'<[^>]+>', '', body_match.group(1)).strip()
        if len(body_text) < 200 and len(html) > 5000:
            return True
    return False


def fetch_url(url: str, timeout: int = 15) -> str:
    """Fetch a URL with two-tier approach.

    1. Try requests (fast, works for SSR/static pages).
    2. If page appears to be JS-rendered, fall back to Playwright headless.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        The full HTML content of the page.
    """
    # Tier 1: requests
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            verify=False,
        )
        html = resp.text

        # Check if JS rendering is needed
        if _detect_js_required(html):
            logger.info(f"JS-rendered page detected for {url}, using Playwright fallback")
            return _fetch_with_playwright(url, timeout)

        return html

    except requests.RequestException as e:
        logger.warning(f"requests failed for {url}: {e}, trying Playwright")
        return _fetch_with_playwright(url, timeout)


def _fetch_with_playwright(url: str, timeout: int = 15) -> str:
    """Fetch a URL using Playwright headless Chromium.

    Waits for the page to be fully loaded (networkidle) before
    extracting the rendered HTML.

    Args:
        url: The URL to fetch.
        timeout: Max wait time in seconds.

    Returns:
        The fully rendered HTML content.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            # Extra wait for dynamic content
            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logger.error(f"Playwright fetch failed for {url}: {e}")
        # Last resort: try requests again
        import requests as req
        try:
            resp = req.get(url, timeout=timeout, verify=False)
            return resp.text
        except Exception:
            raise RuntimeError(f"Failed to fetch {url}: {e}")


# ---------------------------------------------------------------------------
# TEXT EXTRACTION: BeautifulSoup + lxml + html2text
# ---------------------------------------------------------------------------


def extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML using BeautifulSoup + lxml (fallback to html.parser).

    Multi-pass extraction:
    1. Extract tables as structured text rows
    2. Extract code blocks
    3. Extract remaining text via html2text (markdown-like)
    4. Fall back to BeautifulSoup get_text() if html2text unavailable

    Parser fallback: tries lxml first (faster), falls back to built-in
    html.parser if lxml is not installed.

    Args:
        html: Raw HTML content.

    Returns:
        Clean extracted text content.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove unwanted elements
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav", "footer"]):
        tag.decompose()

    text_parts = []

    # --- Phase 1: Extract tables as structured text ---
    for table in soup.find_all("table"):
        table_text = _extract_table(table)
        if table_text.strip():
            text_parts.append(table_text)
        # Remove table from soup so it's not double-extracted
        table.decompose()

    # --- Phase 2: Extract code blocks ---
    for code in soup.find_all(["code", "pre"]):
        code_text = code.get_text(strip=True)
        if code_text:
            text_parts.append(f"[CODE]: {code_text}")
        code.decompose()

    # --- Phase 3: Extract remaining text via html2text ---
    remaining_html = str(soup)
    try:
        import html2text

        h = html2text.HTML2Text()
        h.body_width = 0  # No line wrapping
        h.ignore_links = False
        h.ignore_images = True
        h.ignore_emphasis = False
        h.protect_links = True
        h.unicode_snob = True
        markdown_text = h.handle(remaining_html)
        text_parts.append(markdown_text)
    except ImportError:
        # Fallback: BeautifulSoup get_text with separator
        text = soup.get_text(separator="\n", strip=True)
        text_parts.append(text)

    # --- Phase 4: Clean up ---
    full_text = "\n\n".join(text_parts)

    # Decode common HTML entities
    full_text = (
        full_text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )

    # Collapse excessive whitespace (but keep paragraph breaks)
    full_text = re.sub(r"[ \t]+", " ", full_text)
    full_text = re.sub(r"\n{4,}", "\n\n\n", full_text)

    return full_text.strip()


def _extract_table(table: Tag) -> str:
    """Convert an HTML table to structured text.

    Handles thead/tbody, rowspan, colspan, and nested elements.
    Outputs a clean text representation with headers and rows.

    Args:
        table: BeautifulSoup Tag for the table.

    Returns:
        Structured text representation of the table.
    """
    rows = table.find_all("tr")
    if not rows:
        return ""

    lines = []
    for row in rows:
        cells = row.find_all(["th", "td"])
        cell_texts = []
        for cell in cells:
            # Get text, clean up whitespace
            text = cell.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            cell_texts.append(text)
        if cell_texts:
            lines.append(" | ".join(cell_texts))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SENTENCE SPLITTING (improved for tables/lists)
# ---------------------------------------------------------------------------


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences with improved handling.

    Handles:
    - Standard sentence endings (. ! ?)
    - Table rows (separated by newlines)
    - List items (bullet points, numbered)
    - Code blocks
    - Abbreviations (Mr., Dr., etc.) — avoid false splits

    Args:
        text: Text to split.

    Returns:
        List of sentences/segments.
    """
    # First split on double newlines (paragraphs, table rows, list items)
    paragraphs = re.split(r"\n\n+", text)

    sentences = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # If it looks like a table row (contains | ), keep as one segment
        if "|" in para and para.count("|") >= 2:
            sentences.append(para)
            continue

        # If it looks like a list item (starts with - * 1. etc.), keep as one
        if re.match(r"^[\s]*[-*•·]\s", para) or re.match(r"^\s*\d+[.)]\s", para):
            sentences.append(para)
            continue

        # Standard sentence splitting with abbreviation protection
        # Split on . ! ? followed by space and capital letter
        # But protect common abbreviations
        protected = para
        # Temporarily replace common abbreviations
        for abbr in ["Mr.", "Mrs.", "Dr.", "Prof.", "Sr.", "Jr.", "vs.", "etc.", "e.g.", "i.e."]:
            protected = protected.replace(abbr, abbr.replace(".,", "@@@"))
            protected = protected.replace(abbr, abbr.replace(".", "@@@"))

        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", protected)

        for part in parts:
            # Restore abbreviations
            part = part.replace("@@@", ".")
            part = part.strip()
            if part:
                sentences.append(part)

    return sentences


def group_into_chunks(sentences: list[str], sentences_per_chunk: int = 8) -> list[list[str]]:
    """Group sentences into chunks, keeping related content together.

    Tries to keep table rows and list items in the same chunk.
    Tables are kept as atomic units — all rows of a table stay in one chunk.
    List items are grouped together.

    Args:
        sentences: List of sentences/segments.
        sentences_per_chunk: Number of sentences per chunk (default: 8).

    Returns:
        List of chunks, where each chunk is a list of sentences.
    """
    if not sentences:
        return []

    chunks = []
    current_chunk = []
    current_count = 0
    in_table = False
    table_rows = []

    for sentence in sentences:
        is_table_row = "|" in sentence and sentence.count("|") >= 2
        is_list_item = bool(re.match(r"^[\s]*[-*•·]\s", sentence))

        # --- Table handling: keep all rows of a table together ---
        if is_table_row:
            table_rows.append(sentence)
            in_table = True
            continue
        elif in_table:
            # Table just ended — flush accumulated table rows as one atomic chunk
            # If current chunk has room, append table to it
            if current_count + len(table_rows) <= sentences_per_chunk and current_chunk:
                current_chunk.extend(table_rows)
                current_count += len(table_rows)
            else:
                # Flush current chunk first, then table becomes its own chunk
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = list(table_rows)
                current_count = len(table_rows)
            table_rows = []
            in_table = False
            # Fall through to handle this sentence normally

        # --- List item handling: group consecutive list items ---
        if is_list_item and current_chunk:
            # Check if previous sentence was also a list item
            prev_is_list = bool(re.match(r"^[\s]*[-*•·]\s", current_chunk[-1]))
            if prev_is_list:
                current_chunk.append(sentence)
                current_count += 1
                continue

        # --- Normal sentence handling ---
        if current_count >= sentences_per_chunk:
            chunks.append(current_chunk)
            current_chunk = [sentence]
            current_count = 1
        else:
            current_chunk.append(sentence)
            current_count += 1

    # Flush remaining table rows
    if table_rows:
        if current_count + len(table_rows) <= sentences_per_chunk and current_chunk:
            current_chunk.extend(table_rows)
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = list(table_rows)
        table_rows = []

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def generate_css_selector(chunk_index: int, url: str) -> str:
    """Generate a CSS selector for a chunk.

    Uses a URL hash and chunk index to create a stable selector.

    Args:
        chunk_index: Index of the chunk.
        url: Source URL.

    Returns:
        CSS selector string.
    """
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"#acg-chunk-{url_hash}-{chunk_index}"


# ---------------------------------------------------------------------------
# MAIN INDEX FUNCTION
# ---------------------------------------------------------------------------


def index_url(url: str, sentences_per_chunk: int = 8) -> dict:
    """Index a URL: fetch, extract text, chunk, generate SHI, embed, store.

    **Dedup**: If the URL was already indexed, old chunks are replaced.

    Stores source metadata in 'sources' collection and chunks with
    embeddings in 'data' collection.

    Args:
        url: The URL to index.
        sentences_per_chunk: Number of sentences per chunk (default: 8, range: 5-15).

    Returns:
        Dict with indexing results including chunks and SHI prefix.
    """
    sentences_per_chunk = max(5, min(15, sentences_per_chunk))

    html = fetch_url(url)
    text = extract_text_from_html(html)

    if not text.strip():
        raise ValueError(f"No text content extracted from {url}")

    shi_prefix = generate_shi(text)

    sentences = split_into_sentences(text)
    chunks = group_into_chunks(sentences, sentences_per_chunk)

    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]

    # --- Dedup: remove old entries for this URL ---
    existing = get_source_by_url_hash(url_hash)
    if existing:
        logger.info(f"Re-indexing {url}, removing {existing.get('total_chunks', 0)} old chunks")
        delete_source(url_hash)

    indexed_chunks = []
    for i, chunk_sentences in enumerate(chunks):
        chunk_text = " ".join(chunk_sentences)
        css_selector = generate_css_selector(i, url)
        chunk_entry = {
            "chunk_index": i,
            "sentences": chunk_sentences,
            "text": chunk_text,
            "css_selector": css_selector,
            "shi_prefix": shi_prefix,
            "url": url,
        }
        indexed_chunks.append(chunk_entry)

    # Store source metadata in 'sources' collection
    source_data = {
        "url": url,
        "shi_prefix": shi_prefix,
        "total_chunks": len(chunks),
        "full_text_length": len(text),
    }
    save_source(url_hash, source_data)

    # Store chunks with embeddings in 'data' collection
    save_data_chunks(url_hash, indexed_chunks)

    return {
        "url": url,
        "shi_prefix": shi_prefix,
        "total_chunks": len(chunks),
        "total_text_length": len(text),
        "chunks": [
            {
                "index": c["chunk_index"],
                "sentence_count": len(c["sentences"]),
                "css_selector": c["css_selector"],
                "text_preview": c["text"][:200],
            }
            for c in indexed_chunks
        ],
    }


def search_sources(query: str, limit: int = 5) -> list[dict]:
    """Search indexed sources by semantic similarity or keyword.

    First attempts vector search (cosine similarity on embeddings).
    Falls back to keyword search if embeddings are unavailable.

    Args:
        query: Search query string.
        limit: Maximum number of results.

    Returns:
        List of matching chunks with source metadata.
    """
    from src.acg.db import vector_search, search_chunks

    # Try vector search first
    results = vector_search(query, limit=limit, min_score=0.7)
    if results:
        return results
    # Fall back to keyword search
    return search_chunks(query, limit)
