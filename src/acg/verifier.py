"""Verification agent module — v3 (chunk-scoped, number-strict matching).

Parses Claim Markers from grounded text, re-fetches each source to confirm
its Source Hash Identity (SHI), then checks the claim against the **chunk
the marker points at** (``css=#acg-chunk-<url_hash>-<index>``), not the
whole page.

Matching rules, in order:

1. ``exact``    — the claim (normalised) occurs verbatim in the chunk, or in
                  the chunk joined with its neighbours, on word boundaries.
2. Number check — every standalone number in the claim ("64", "5.0",
                  "$0.14") must occur as a whole token in the target chunk.
                  Identifier-like tokens with digits ("qwen3", "x16") must
                  occur in the chunk or elsewhere in the source. Any miss
                  → ``contradicted``.
3. Coverage     — fraction of the claim's content words present in the
                  chunk. ``>= SUPPORTED_THRESHOLD`` → ``supported``;
                  ``>= PARTIAL_THRESHOLD`` → ``partial``; else ``not_found``.

Only ``exact`` and ``supported`` count as ``verified: true``. A marker that
does not resolve to an indexed chunk is ``unlocated`` and never verified.

Uses collections: sources, data, claims.
"""

import re
from typing import Optional

from src.acg.ugvp import CLAIM_MARKER_PATTERN
from src.acg.rsvp import RELATIONSHIP_MARKER_PATTERN
from src.acg.shi import verify_shi
from src.acg.indexer import fetch_url, extract_text_from_html
from src.acg.db import get_source, get_chunk_window, save_claim, _STOPWORDS


SUPPORTED_THRESHOLD = 0.8
PARTIAL_THRESHOLD = 0.5
VERIFIED_STATUSES = frozenset({"exact", "supported"})

CHUNK_SELECTOR_PATTERN = re.compile(r"^#acg-chunk-([0-9a-f]{8})-(\d+)$")

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")
_PURE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)*")
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


# ---------------------------------------------------------------------------
# Tokenisation helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase, drop thousands separators, collapse whitespace."""
    text = _THOUSANDS_SEP_RE.sub("", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> list[str]:
    """Tokenize normalised text; decimals like ``4.0`` stay one token."""
    return _TOKEN_RE.findall(_normalize(text))


def _is_pure_number(token: str) -> bool:
    return bool(_PURE_NUMBER_RE.fullmatch(token))


def _has_digit(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def _contains_on_word_boundary(needle: str, haystack: str) -> bool:
    """True if ``needle`` occurs in ``haystack`` not glued to letters/digits.

    Prevents "number of layers: 6" from matching "number of layers: 64".
    """
    if not needle:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def match_claim(
    claim_text: str,
    chunk_text: str,
    window_text: str = "",
    source_text: str = "",
) -> dict:
    """Score a claim against the chunk its marker points at.

    Args:
        claim_text: The claim to check.
        chunk_text: Text of the target chunk.
        window_text: Target chunk joined with its neighbours (for claims that
            straddle a chunk boundary). Used for the exact check only.
        source_text: Full source text. Used only to locate identifier-like
            tokens (e.g. a model name in the page title).

    Returns:
        Dict with ``status``, ``similarity`` (0-1) and, for contradictions,
        ``missing_numbers``.
    """
    claim_norm = _normalize(claim_text).rstrip(".!?;:, ")
    chunk_norm = _normalize(chunk_text)
    window_norm = _normalize(window_text) if window_text else chunk_norm

    if not claim_norm:
        return {"status": "not_found", "similarity": 0.0}

    if _contains_on_word_boundary(claim_norm, chunk_norm) or _contains_on_word_boundary(
        claim_norm, window_norm
    ):
        return {"status": "exact", "similarity": 1.0}

    claim_tokens = _tokenize(claim_text)
    chunk_tokens = set(_tokenize(chunk_text))
    source_tokens = set(_tokenize(source_text)) if source_text else chunk_tokens

    missing = []
    for tok in dict.fromkeys(claim_tokens):  # ordered, de-duplicated
        if not _has_digit(tok):
            continue
        if _is_pure_number(tok):
            if tok not in chunk_tokens:
                missing.append(tok)
        elif tok not in chunk_tokens and tok not in source_tokens:
            missing.append(tok)

    content = {t for t in claim_tokens if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())}
    if not content:
        content = set(claim_tokens)
    coverage = len(content & chunk_tokens) / len(content) if content else 0.0
    similarity = round(coverage, 4)

    if missing:
        return {"status": "contradicted", "similarity": similarity, "missing_numbers": missing}
    if coverage >= SUPPORTED_THRESHOLD:
        return {"status": "supported", "similarity": similarity}
    if coverage >= PARTIAL_THRESHOLD:
        return {"status": "partial", "similarity": similarity}
    return {"status": "not_found", "similarity": similarity}


# ---------------------------------------------------------------------------
# Claim text extraction
# ---------------------------------------------------------------------------


def _extract_claim_texts(text: str) -> list[Optional[str]]:
    """Extract the claim text for every Claim Marker, in marker order.

    A claim is bounded by the end of the previous marker (claim or
    relationship) and the last newline before its own marker; within that
    segment, the last sentence is taken.

    Args:
        text: The full grounded text.

    Returns:
        One entry per Claim Marker (aligned with ``parse_claim_markers``).
    """
    boundaries = sorted(
        [(m.start(), m.end(), "C") for m in CLAIM_MARKER_PATTERN.finditer(text)]
        + [(m.start(), m.end(), "R") for m in RELATIONSHIP_MARKER_PATTERN.finditer(text)]
    )

    texts: list[Optional[str]] = []
    prev_end = 0
    for start, end, kind in boundaries:
        if kind == "C":
            segment = text[prev_end:start].rstrip()
            nl = segment.rfind("\n")
            if nl != -1:
                segment = segment[nl + 1:]
            segment = segment.strip()
            if segment:
                sentences = re.split(r"(?<=[.!?])\s+", segment)
                claim = sentences[-1].strip()
                texts.append(claim or None)
            else:
                texts.append(None)
        prev_end = end
    return texts


def _extract_claim_text(text: str, claim_id: int) -> Optional[str]:
    """Extract the claim text for the first marker with ``claim_id``.

    Kept for backward compatibility; prefer ``_extract_claim_texts``.
    """
    for marker, claim in zip(CLAIM_MARKER_PATTERN.finditer(text), _extract_claim_texts(text)):
        if int(marker.group(1)) == claim_id:
            return claim
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _resolve_chunk(source_entry: dict, css_selector: str) -> tuple[Optional[str], str, str]:
    """Resolve a marker selector to (chunk_text, window_text, reason)."""
    m = CHUNK_SELECTOR_PATTERN.match(css_selector.strip())
    if not m:
        return None, "", f"Selector '{css_selector}' does not reference an indexed chunk"
    url_hash, idx = m.group(1), int(m.group(2))
    if source_entry.get("url_hash") and url_hash != source_entry["url_hash"]:
        return None, "", f"Selector '{css_selector}' belongs to a different source than the SHI prefix"
    window = get_chunk_window(url_hash, idx)
    if not window:
        return None, "", f"Chunk '{css_selector}' not found in data collection"
    target = next(c for c in window if c.get("chunk_index") == idx)
    window_text = " ".join(c.get("text", "") for c in window)
    return target.get("text", ""), window_text, ""


def verify_claims(text: str) -> dict:
    """Verify all claims in grounded text against their sources.

    For each Claim Marker: look up the source by SHI prefix, re-fetch it to
    confirm the SHI, resolve the marker's chunk, and score the claim against
    that chunk (see module docstring for the rules).

    Saves verification results to the 'claims' collection.

    Args:
        text: Grounded text containing Claim Markers.

    Returns:
        Dict with verification results. Each per-claim result carries
        ``verified`` (bool), ``status``, ``similarity`` and ``match_method``.
    """
    markers = list(CLAIM_MARKER_PATTERN.finditer(text))
    if not markers:
        return {
            "verified": False,
            "total_claims": 0,
            "passed": 0,
            "failed": 0,
            "results": [],
            "error": "No claim markers found in text",
        }

    claim_texts = _extract_claim_texts(text)
    results = []

    for marker, claim_text in zip(markers, claim_texts):
        claim_id = f"C{marker.group(1)}"
        shi_prefix = marker.group(2)
        css_selector = marker.group(3)

        record = {
            "claim_id": claim_id,
            "shi_prefix": shi_prefix,
            "source_url": "",
            "css_selector": css_selector,
            "claim_text": claim_text or "",
        }

        source_entry = get_source(shi_prefix)
        if not source_entry:
            result = {
                "claim_id": claim_id,
                "shi_prefix": shi_prefix,
                "verified": False,
                "status": "unlocated",
                "reason": f"Source with SHI prefix '{shi_prefix}' not found in sources collection",
            }
            results.append(result)
            save_claim({**record, "verified": False, "status": "unlocated", "reason": result["reason"]})
            continue

        record["source_url"] = source_entry.get("url", "")

        try:
            html = fetch_url(source_entry["url"])
            source_text = extract_text_from_html(html)
            shi_valid = verify_shi(source_text, shi_prefix)

            chunk_text, window_text, reason = _resolve_chunk(source_entry, css_selector)
            if chunk_text is None:
                match = {"status": "unlocated", "similarity": 0.0, "reason": reason}
            elif not claim_text:
                match = {"status": "not_found", "similarity": 0.0, "reason": "No claim text before marker"}
            else:
                match = match_claim(claim_text, chunk_text, window_text, source_text)

            status = match["status"]
            verified = shi_valid and status in VERIFIED_STATUSES
            result = {
                "claim_id": claim_id,
                "shi_prefix": shi_prefix,
                "source_url": source_entry["url"],
                "shi_valid": shi_valid,
                "claim_exists_in_source": status in VERIFIED_STATUSES,
                "status": status,
                "match_method": status,
                "similarity": match["similarity"],
                "claim_text": claim_text,
                "verified": verified,
            }
            if "missing_numbers" in match:
                result["missing_numbers"] = match["missing_numbers"]
            if match.get("reason"):
                result["reason"] = match["reason"]
            elif not shi_valid:
                result["reason"] = "Source content changed since indexing (SHI mismatch)"
            results.append(result)

            save_claim({
                **record,
                "verified": verified,
                "status": status,
                "match_method": status,
                "similarity": match["similarity"],
            })
        except Exception as e:
            result = {
                "claim_id": claim_id,
                "shi_prefix": shi_prefix,
                "verified": False,
                "status": "error",
                "reason": f"Error re-fetching source: {str(e)}",
            }
            results.append(result)
            save_claim({**record, "verified": False, "status": "error", "reason": result["reason"]})

    passed = sum(1 for r in results if r.get("verified"))
    failed = len(results) - passed

    return {
        "verified": failed == 0,
        "total_claims": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }
