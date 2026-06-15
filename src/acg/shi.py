"""Source Hash Identity (SHI) module.

Generates SHA256-based identity prefixes for source content.
The SHI prefix is the first 12 characters of the SHA256 hex digest,
providing a compact, verifiable fingerprint for each source.
"""

import hashlib


def generate_shi(content: str) -> str:
    """Generate a Source Hash Identity prefix from content.

    Args:
        content: The full text content of the source.

    Returns:
        First 12 characters of the SHA256 hex digest.
    """
    if not content:
        raise ValueError("Content cannot be empty for SHI generation")
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return sha[:12]


def generate_shi_full(content: str) -> str:
    """Generate the full SHA256 hex digest.

    Args:
        content: The full text content of the source.

    Returns:
        Full 64-character SHA256 hex digest.
    """
    if not content:
        raise ValueError("Content cannot be empty for SHI generation")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def verify_shi(content: str, shi_prefix: str) -> bool:
    """Verify that content matches a given SHI prefix.

    Args:
        content: The content to verify.
        shi_prefix: The expected SHI prefix (12 chars).

    Returns:
        True if the prefix matches, False otherwise.
    """
    computed = generate_shi(content)
    return computed == shi_prefix
