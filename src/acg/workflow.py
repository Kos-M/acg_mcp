"""Enforced ACG workflow — single-call audited pipeline.

Runs the full Audited Context Generation pipeline in ONE call:

    1. search    — search indexed sources for the query (check_indexed logic)
    2. index     — if confidence is LOW and a URL was provided, index it
                   first, then re-search (auto-fetch)
    3. ground    — compose a grounded answer with UGVP Claim Markers
    4. verify    — re-fetch every source and fuzzy-match each claim (audit)
    5. audit     — build the Veracity Audit Registry (SSR + RAR)

The result is a self-auditing report: the grounded answer, per-claim
verification, a Chunk Signatures Table, [Claims Verified], [ACG Accuracy]
and [ACG Signed] footer — so any consumer can independently audit it.

This is the *enforcement* layer of the protocol. The individual ACG tools
(index, search, ground, verify, VAR) remain fully available for custom
pipelines; use this module when you want the protocol followed end-to-end
without having to orchestrate the steps yourself.
"""

import logging

from src.acg.shi import generate_shi
from src.acg.ugvp import make_claim_marker, parse_claim_markers, make_ssr_entry
from src.acg.rsvp import parse_relationship_markers, make_rar_entry
from src.acg.var import build_var
from src.acg import indexer, verifier

logger = logging.getLogger(__name__)

WORKFLOW_STEPS = ["search", "ground", "verify", "audit"]
WORKFLOW_STEPS_WITH_INDEX = ["index", "search", "ground", "verify", "audit"]
SIGNED_BY = "ACG Protocol"


def _compute_confidence(results: list) -> float:
    """Normalize search results into a 0-1 confidence score.

    Keyword search scores are 0-10; vector search scores are 0-1.
    A single score is dominated by the top hit and capped at 1.0.

    Args:
        results: List of search result dicts with a 'score' key.

    Returns:
        Confidence in [0, 1].
    """
    if not results:
        return 0.0
    best = max(r.get("score", 0) for r in results)
    # Keyword search scales 0-10, vector search scales 0-1.
    normalized = best / 10.0 if best > 1.0 else best
    return round(min(normalized, 1.0), 4)


def _confidence_tier(confidence: float) -> str:
    """Map a confidence score to a tier label.

    Args:
        confidence: Confidence score in [0, 1].

    Returns:
        "HIGH", "MEDIUM", or "LOW".
    """
    if confidence >= 0.7:
        return "HIGH"
    if confidence >= 0.4:
        return "MEDIUM"
    return "LOW"


def _compose_grounded_answer(results: list, max_claims: int = 3) -> str:
    """Compose a grounded answer from search results.

    Each top result becomes a claim sentence followed by its UGVP
    Claim Marker [C<id>:<shi_prefix>:css=<selector>] so the answer is
    fully verifiable.

    Args:
        results: Search result dicts with url, shi_prefix, css_selector,
                 text, sentences.
        max_claims: Maximum number of claims to include (default: 3).

    Returns:
        Grounded text with inline claim markers.
    """
    lines = []
    for i, r in enumerate(results[:max_claims], start=1):
        text = (r.get("text") or "").strip()
        if not text:
            continue
        sentences = r.get("sentences") or []
        claim_text = " ".join(sentences[:2]).strip() if sentences else text[:300]
        marker = make_claim_marker(
            i, r.get("shi_prefix", ""), r.get("css_selector", "#acg-auto")
        )
        lines.append(f"{claim_text} {marker}")
    return "\n\n".join(lines)


def _build_chunk_signatures(verification_results: list) -> list:
    """Build the Chunk Signatures Table entries from verification results.

    Args:
        verification_results: Per-claim results from verify_claims().

    Returns:
        List of dicts: claim_id, shi_prefix, source_url, verified.
    """
    return [
        {
            "claim_id": r.get("claim_id", ""),
            "shi_prefix": r.get("shi_prefix", ""),
            "source_url": r.get("source_url", ""),
            "verified": bool(r.get("verified", False)),
        }
        for r in verification_results
    ]


def format_audit_footer(report: dict) -> str:
    """Render the human-readable audit footer for a workflow report.

    The footer lets any consumer audit the answer independently:
    Chunk Signatures Table + Claims Verified + ACG Accuracy + signature.

    Args:
        report: The report dict returned by run_workflow().

    Returns:
        Multi-line audit footer string.
    """
    lines = ["Chunk Signatures Table", "| Claim | SHI Prefix | Source | Verified |", "|---|---|---|---|"]
    for s in report.get("chunk_signatures", []):
        lines.append(f"| {s['claim_id']} | {s['shi_prefix']} | {s['source_url']} | {s['verified']} |")
    lines.append(f"[Claims Verified: {report.get('claims_verified')}]")
    lines.append(f"[ACG Accuracy: {report.get('acg_accuracy')}%]")
    lines.append(f"[ACG Signed: {report.get('acg_signed')}]")
    return "\n".join(lines)


def run_workflow(
    query: str,
    url: str = "",
    auto_index: bool = True,
    sentences_per_chunk: int = 8,
) -> dict:
    """Run the enforced ACG workflow end-to-end and return an audited report.

    Args:
        query: The user question to answer from indexed sources.
        url: Optional URL to index first when confidence is LOW.
        auto_index: If True (default) and a url is given, index it on LOW
                    confidence before giving up.
        sentences_per_chunk: Chunk size used when indexing (5-15).

    Returns:
        Report dict with:
        - query, workflow steps, confidence tier, answerable
        - grounded_answer (with Claim Markers)
        - verification summary + per-claim results
        - chunk_signatures (Chunk Signatures Table)
        - claims_verified ("x/y"), acg_accuracy (%), acg_signed
        - var (Veracity Audit Registry)
    """
    steps = list(WORKFLOW_STEPS)
    indexed_url = ""

    # 1. SEARCH
    results = indexer.search_sources(query, limit=5)
    confidence = _compute_confidence(results)
    tier = _confidence_tier(confidence)

    # 2. INDEX (auto-fetch) when confidence is LOW and a URL is available
    if tier == "LOW" and url and auto_index:
        logger.info(f"LOW confidence for '{query}'; indexing {url}")
        indexer.index_url(url, sentences_per_chunk)
        indexed_url = url
        steps = list(WORKFLOW_STEPS_WITH_INDEX)
        results = indexer.search_sources(query, limit=5)
        confidence = _compute_confidence(results)
        tier = _confidence_tier(confidence)

    if not results:
        return {
            "query": query,
            "workflow": steps,
            "confidence": 0.0,
            "confidence_tier": "LOW",
            "answerable": False,
            "instruction": (
                "No matching sources found. Index a URL first "
                "(acg_index_url, or pass url= to this tool) or use "
                "acg_crawl_and_index."
            ),
            "indexed_url": indexed_url,
            "grounded_answer": "",
            "verification": {"total_claims": 0, "passed": 0, "failed": 0},
            "chunk_signatures": [],
            "claims_verified": "0/0",
            "acg_accuracy": 0.0,
            "acg_signed": SIGNED_BY,
            "var": {},
        }

    # 3. GROUND
    grounded = _compose_grounded_answer(results)

    # 4. VERIFY
    verification = verifier.verify_claims(grounded) if grounded else {
        "verified": False, "total_claims": 0, "passed": 0, "failed": 0, "results": []
    }
    ver_results = verification.get("results", [])
    total = verification.get("total_claims", len(ver_results))
    passed = verification.get("passed", sum(1 for r in ver_results if r.get("verified")))
    accuracy = round((passed / total) * 100.0, 1) if total else 0.0

    # 5. AUDIT — build the Veracity Audit Registry (SSR + RAR)
    markers = parse_claim_markers(grounded)
    ver_by_id = {r.get("claim_id"): r for r in ver_results}
    ssr_entries = [
        make_ssr_entry(
            claim_id=m["claim_id"],
            shi_prefix=m["shi_prefix"],
            source_url=ver_by_id.get(f"C{m['claim_id']}", {}).get("source_url", ""),
            css_selector=m["css_selector"],
            claim_text=ver_by_id.get(f"C{m['claim_id']}", {}).get("claim_text", ""),
            verified=bool(ver_by_id.get(f"C{m['claim_id']}", {}).get("verified", False)),
        )
        for m in markers
    ]
    rel_markers = parse_relationship_markers(grounded)
    rar_entries = [
        make_rar_entry(
            rel_id=m["rel_id"],
            rel_type=m["rel_type"],
            claim_ids=m["claim_ids"],
            synthesis_text="",
        )
        for m in rel_markers
    ]
    var = build_var(ssr_entries, rar_entries)

    return {
        "query": query,
        "workflow": steps,
        "confidence": confidence,
        "confidence_tier": tier,
        "answerable": tier != "LOW",
        "indexed_url": indexed_url,
        "grounded_answer": grounded,
        "verification": verification,
        "chunk_signatures": _build_chunk_signatures(ver_results),
        "claims_verified": f"{passed}/{total}",
        "acg_accuracy": accuracy,
        "acg_signed": SIGNED_BY,
        "var": var,
    }
