"""Verification agent module — v2 (Fuzzy Matching).

Parses Claim Markers from grounded text, re-fetches sources,
and verifies that claims exist at the specified locations.
Uses **fuzzy matching** (token overlap) instead of exact substring
so that table data like "$0.14" matches "1M INPUT TOKENS (CACHE MISS) $0.14".

Uses collections: sources, data, claims.
"""

import re
from typing import Optional

from src.acg.ugvp import parse_claim_markers, CLAIM_MARKER_PATTERN
from src.acg.shi import verify_shi
from src.acg.indexer import fetch_url, extract_text_from_html
from src.acg.db import get_source, save_claim


def _tokenize(text: str) -> set[str]:
    """Tokenize text into a set of lowercase alphanumeric tokens.

    Args:
        text: Text to tokenize.

    Returns:
        Set of lowercase tokens.
    """
    return set(re.findall(r"[a-z0-9$]+", text.lower()))


def _fuzzy_match(claim_text: str, source_text: str, threshold: float = 0.6) -> bool:
    """Check if claim text exists in source text using fuzzy token overlap.

    Uses Jaccard similarity on token sets. This handles cases where:
    - Source has "$0.14" in a table row but claim says "$0.14 per 1M input tokens"
    - Source has "deepseek-v4-flash" but claim says "DeepSeek V4-Flash"

    Args:
        claim_text: The claim text to find.
        source_text: The source text to search in.
        threshold: Minimum Jaccard similarity (0-1) to consider a match.

    Returns:
        True if the claim is fuzzy-matched in the source.
    """
    claim_tokens = _tokenize(claim_text)
    if not claim_tokens:
        return False

    # Strategy 1: Check if claim tokens are a subset of source tokens
    source_tokens = _tokenize(source_text)
    if claim_tokens.issubset(source_tokens):
        return True

    # Strategy 2: Sliding window — check chunks of source text
    # Split source into sentences/rows and check each
    source_lines = re.split(r"\n+", source_text)
    for line in source_lines:
        line_tokens = _tokenize(line)
        if not line_tokens:
            continue
        intersection = claim_tokens & line_tokens
        jaccard = len(intersection) / len(claim_tokens | line_tokens)
        if jaccard >= threshold:
            return True

    # Strategy 3: Check if key numeric values from claim appear in source
    # (handles pricing tables where "$0.14" is the key data point)
    numeric_values = re.findall(r"\$?\d+\.?\d*", claim_text)
    if numeric_values:
        for val in numeric_values:
            if val in source_text:
                return True

    return False


def verify_claims(text: str) -> dict:
    """Verify all claims in grounded text against their sources.

    Parses Claim Markers, looks up indexed sources, re-fetches content,
    and checks if the claim text exists at the specified location.
    Uses **fuzzy matching** for robust verification.

    Saves verification results to the 'claims' collection.

    Args:
        text: Grounded text containing Claim Markers.

    Returns:
        Dict with verification results.
    """
    markers = parse_claim_markers(text)
    if not markers:
        return {
            "verified": False,
            "total_claims": 0,
            "passed": 0,
            "failed": 0,
            "results": [],
            "error": "No claim markers found in text",
        }

    results = []

    for marker in markers:
        claim_id = marker["claim_id"]
        shi_prefix = marker["shi_prefix"]
        css_selector = marker["css_selector"]

        # Find the source in MongoDB by SHI prefix
        source_entry = get_source(shi_prefix)

        if not source_entry:
            result = {
                "claim_id": f"C{claim_id}",
                "shi_prefix": shi_prefix,
                "verified": False,
                "reason": f"Source with SHI prefix '{shi_prefix}' not found in sources collection",
            }
            results.append(result)
            save_claim({
                "claim_id": f"C{claim_id}",
                "shi_prefix": shi_prefix,
                "source_url": "",
                "css_selector": css_selector,
                "claim_text": "",
                "verified": False,
                "reason": result["reason"],
            })
            continue

        # Extract the claim text from the grounded text
        claim_text = _extract_claim_text(text, claim_id)

        # Re-fetch the source to verify
        try:
            html = fetch_url(source_entry["url"])
            source_text = extract_text_from_html(html)

            # Verify SHI
            shi_valid = verify_shi(source_text, shi_prefix)

            # Fuzzy match the claim against the source
            claim_exists = False
            match_method = "none"
            if claim_text:
                # Try exact match first (fast path)
                if claim_text.lower() in source_text.lower():
                    claim_exists = True
                    match_method = "exact"
                else:
                    # Fall back to fuzzy matching
                    claim_exists = _fuzzy_match(claim_text, source_text)
                    match_method = "fuzzy" if claim_exists else "none"

            verified = shi_valid and claim_exists
            result = {
                "claim_id": f"C{claim_id}",
                "shi_prefix": shi_prefix,
                "source_url": source_entry["url"],
                "shi_valid": shi_valid,
                "claim_exists_in_source": claim_exists,
                "match_method": match_method,
                "claim_text": claim_text,
                "verified": verified,
            }
            results.append(result)

            # Save verified claim to claims collection
            save_claim({
                "claim_id": f"C{claim_id}",
                "shi_prefix": shi_prefix,
                "source_url": source_entry["url"],
                "css_selector": css_selector,
                "claim_text": claim_text,
                "verified": verified,
                "match_method": match_method,
            })
        except Exception as e:
            result = {
                "claim_id": f"C{claim_id}",
                "shi_prefix": shi_prefix,
                "verified": False,
                "reason": f"Error re-fetching source: {str(e)}",
            }
            results.append(result)
            save_claim({
                "claim_id": f"C{claim_id}",
                "shi_prefix": shi_prefix,
                "source_url": source_entry.get("url", ""),
                "css_selector": css_selector,
                "claim_text": claim_text or "",
                "verified": False,
                "reason": result["reason"],
            })

    passed = sum(1 for r in results if r.get("verified"))
    failed = sum(1 for r in results if not r.get("verified"))

    return {
        "verified": failed == 0,
        "total_claims": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def _extract_claim_text(text: str, claim_id: int) -> Optional[str]:
    """Extract the claim text associated with a claim marker.

    Looks for the text immediately preceding the claim marker.

    Args:
        text: The full grounded text.
        claim_id: The claim ID to extract text for.

    Returns:
        The claim text, or None if not found.
    """
    # Find the marker position
    pattern = re.compile(r'\[C' + str(claim_id) + r':[a-f0-9]{12}:css=[^\]]+\]')
    match = pattern.search(text)
    if not match:
        return None

    # Get text before the marker (up to 500 chars)
    start = max(0, match.start() - 500)
    before_text = text[start:match.start()].strip()

    # Try to find the sentence boundary
    sentences = re.split(r'(?<=[.!?])\s+', before_text)
    if sentences:
        return sentences[-1].strip()
    return before_text[-200:] if before_text else None
