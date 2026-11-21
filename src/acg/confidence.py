"""Confidence scoring for search results — one source of truth.

``acg_check_indexed`` and the enforced workflow both decide from this
module whether indexed sources can answer a query, so the tier, the
``answerable`` flag and the instruction to the agent can never disagree.

Confidence is the best ``hybrid_score`` among the results (see
``db.hybrid_search``). The hybrid score already requires agreement between
semantic similarity and IDF-weighted keyword coverage. The older measure,
max(cosine, keyword coverage), let off-topic pages that share generic
vocabulary ("agents", "code", "AI") score around 0.7.

Calibration on a ~11k-chunk corpus (bge-small-en-v1.5):
    exact-topic queries         0.80 - 0.86
    partially covered queries   0.50 - 0.55
    off-topic queries           0.35 - 0.40
"""

HIGH_THRESHOLD = 0.7
MEDIUM_THRESHOLD = 0.5

INSTRUCTIONS = {
    "HIGH": (
        "HIGH confidence — indexed sources cover this query. Answer from them "
        "with Claim Markers. Do not use web_fetch."
    ),
    "MEDIUM": (
        "MEDIUM confidence — indexed sources cover part of this query. Answer "
        "only what the matches below support; use web_fetch for the missing "
        "parts and acg_index_url the pages you fetch."
    ),
    "LOW": (
        "LOW confidence — indexed sources do not answer this query (matches "
        "below are likely off-topic). Use web_fetch, then acg_index_url the "
        "pages you use."
    ),
}


def compute_confidence(results: list[dict]) -> float:
    """Return a 0-1 confidence that the results answer the query.

    Uses ``hybrid_score`` when present. Results without it (legacy or
    single-signal callers) fall back to ``score``, where keyword scores on
    the 0-10 scale are divided by 10.

    Args:
        results: Search result dicts.

    Returns:
        Confidence in [0, 1], rounded to 4 places.
    """
    if not results:
        return 0.0
    best = 0.0
    for r in results:
        if r.get("hybrid_score") is not None:
            value = float(r["hybrid_score"])
        else:
            value = float(r.get("score", 0.0) or 0.0)
            if value > 1.0:
                value /= 10.0
        best = max(best, value)
    return round(min(best, 1.0), 4)


def confidence_tier(confidence: float) -> str:
    """Map a confidence score to HIGH / MEDIUM / LOW."""
    if confidence >= HIGH_THRESHOLD:
        return "HIGH"
    if confidence >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def is_answerable(tier: str) -> bool:
    """Whether indexed sources can (at least partly) answer the query."""
    return tier != "LOW"


def assess(results: list[dict]) -> dict:
    """Confidence, tier, answerable flag and instruction for a result set."""
    confidence = compute_confidence(results)
    tier = confidence_tier(confidence)
    return {
        "confidence": confidence,
        "tier": tier,
        "answerable": is_answerable(tier),
        "instruction": INSTRUCTIONS[tier],
    }
