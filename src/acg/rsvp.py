"""RSVP (Relationship Synthesis Verification Protocol) module.

Handles Relationship Markers and Relationship Audit Records (RAR).
Relationships connect multiple claims: (R1:TYPE:C1,C2)
"""

import re
from typing import Optional


RELATIONSHIP_TYPES = {"SUMMARY", "CONTRADICTION", "EXTENSION", "CAUSAL", "ANALOGY"}

RELATIONSHIP_MARKER_PATTERN = re.compile(
    r'\(R(\d+):(' + '|'.join(RELATIONSHIP_TYPES) + r'):(C\d+(?:,C\d+)*)\)'
)
RELATIONSHIP_MARKER_TEMPLATE = "(R{rel_id}:{rel_type}:{claim_ids})"


def make_relationship_marker(
    rel_id: int,
    rel_type: str,
    claim_ids: list[str],
) -> str:
    """Create an RSVP Relationship Marker string.

    Args:
        rel_id: Sequential relationship ID (e.g., 1, 2, 3).
        rel_type: Type of relationship (SUMMARY, CONTRADICTION, etc.).
        claim_ids: List of claim IDs (e.g., ["C1", "C2"]).

    Returns:
        Formatted relationship marker string.
    """
    if rel_type not in RELATIONSHIP_TYPES:
        raise ValueError(
            f"Invalid relationship type '{rel_type}'. "
            f"Must be one of: {', '.join(sorted(RELATIONSHIP_TYPES))}"
        )
    return RELATIONSHIP_MARKER_TEMPLATE.format(
        rel_id=rel_id,
        rel_type=rel_type,
        claim_ids=",".join(claim_ids),
    )


def parse_relationship_markers(text: str) -> list[dict]:
    """Parse all Relationship Markers from text.

    Args:
        text: Text containing relationship markers.

    Returns:
        List of dicts with keys: rel_id, rel_type, claim_ids.
    """
    matches = RELATIONSHIP_MARKER_PATTERN.findall(text)
    return [
        {
            "rel_id": int(m[0]),
            "rel_type": m[1],
            "claim_ids": m[2].split(","),
        }
        for m in matches
    ]


def make_rar_entry(
    rel_id: int,
    rel_type: str,
    claim_ids: list[str],
    synthesis_text: str,
) -> dict:
    """Create a Relationship Audit Record (RAR) entry.

    Args:
        rel_id: Sequential relationship ID.
        rel_type: Type of relationship.
        claim_ids: List of claim IDs involved.
        synthesis_text: The synthesized text describing the relationship.

    Returns:
        RAR entry dict.
    """
    return {
        "relationship_id": f"R{rel_id}",
        "type": rel_type,
        "claim_ids": claim_ids,
        "synthesis_text": synthesis_text,
    }
