"""UGVP (Unified Grounded Verification Protocol) module.

Handles Claim Markers and Source Status Records (SSR).
Each claim from a source gets a marker: [C1:shi_prefix:css=selector]
"""

import re
from typing import Optional


CLAIM_MARKER_PATTERN = re.compile(r'\[C(\d+):([a-f0-9]{12}):css=([^\]]+)\]')
CLAIM_MARKER_TEMPLATE = "[C{claim_id}:{shi_prefix}:css={css_selector}]"


def make_claim_marker(claim_id: int, shi_prefix: str, css_selector: str) -> str:
    """Create a UGVP Claim Marker string.

    Args:
        claim_id: Sequential claim ID (e.g., 1, 2, 3).
        shi_prefix: First 12 chars of SHA256 of source content.
        css_selector: CSS selector pinpointing the source location.

    Returns:
        Formatted claim marker string.
    """
    return CLAIM_MARKER_TEMPLATE.format(
        claim_id=claim_id,
        shi_prefix=shi_prefix,
        css_selector=css_selector,
    )


def parse_claim_markers(text: str) -> list[dict]:
    """Parse all Claim Markers from text.

    Args:
        text: Text containing claim markers.

    Returns:
        List of dicts with keys: claim_id, shi_prefix, css_selector.
    """
    matches = CLAIM_MARKER_PATTERN.findall(text)
    return [
        {
            "claim_id": int(m[0]),
            "shi_prefix": m[1],
            "css_selector": m[2],
        }
        for m in matches
    ]


def make_ssr_entry(
    claim_id: int,
    shi_prefix: str,
    source_url: str,
    css_selector: str,
    claim_text: str,
    verified: bool = False,
) -> dict:
    """Create a Source Status Record (SSR) entry.

    Args:
        claim_id: Sequential claim ID.
        shi_prefix: SHI prefix of the source.
        source_url: URL of the source.
        css_selector: CSS selector for the claim location.
        claim_text: The actual claim text.
        verified: Whether the claim has been verified.

    Returns:
        SSR entry dict.
    """
    return {
        "claim_id": f"C{claim_id}",
        "shi_prefix": shi_prefix,
        "source_url": source_url,
        "css_selector": css_selector,
        "claim_text": claim_text,
        "verified": verified,
    }
