"""VAR (Veracity Audit Registry) module.

Builds the complete audit trail JSON combining SSR and RAR entries.
The VAR provides a machine-readable record of all claims and relationships.
"""

import json
import datetime
from typing import Optional


def build_var(
    ssr_entries: list[dict],
    rar_entries: Optional[list[dict]] = None,
) -> dict:
    """Build a complete Veracity Audit Registry.

    Args:
        ssr_entries: List of Source Status Record entries from UGVP.
        rar_entries: Optional list of Relationship Audit Record entries from RSVP.

    Returns:
        Complete VAR dict ready for JSON serialization.
    """
    var = {
        "protocol": "ACG/1.0",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ssr_entries": ssr_entries,
        "rar_entries": rar_entries or [],
    }
    return var


def var_to_json(var: dict, indent: int = 2) -> str:
    """Serialize a VAR dict to formatted JSON string.

    Args:
        var: The VAR dict from build_var().
        indent: JSON indentation level.

    Returns:
        Formatted JSON string.
    """
    return json.dumps(var, indent=indent, ensure_ascii=False)


def var_from_json(json_str: str) -> dict:
    """Deserialize a VAR from JSON string.

    Args:
        json_str: JSON string of a VAR.

    Returns:
        VAR dict.
    """
    return json.loads(json_str)
