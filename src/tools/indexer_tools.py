"""MCP tools for URL indexing and source searching.

Uses FastMCP @mcp.tool() decorator pattern.
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

from src.acg.indexer import index_url as _index_url
from src.acg.indexer import search_sources as _search_sources
from src.acg.db import list_sources, count_sources, get_source_by_url, get_chunks_by_source

logger = logging.getLogger(__name__)


def _compute_confidence(results: list, total_indexed: int) -> float:
    if not results or total_indexed == 0:
        return 0.0
    best_score = max(r.get("score", 0) for r in results) if results else 0
    coverage = min(len(results) / max(total_indexed, 1), 1.0)
    return min((best_score * 0.7 + coverage * 0.3), 1.0)


def _get_confidence_tier(confidence: float) -> str:
    if confidence >= 0.7:
        return "HIGH"
    elif confidence >= 0.4:
        return "MEDIUM"
    return "LOW"


def _expand_chunk(chunk: dict, n: int = 2) -> str:
    url = chunk.get("url", "")
    chunk_index = chunk.get("chunk_index")
    if not url or chunk_index is None:
        return chunk.get("text", "")
    source = get_source_by_url(url)
    if not source:
        return chunk.get("text", "")
    source_id = source.get("url_hash", "")
    if not source_id:
        return chunk.get("text", "")
    all_chunks = get_chunks_by_source(source_id)
    if not all_chunks:
        return chunk.get("text", "")
    chunk_map = {c.get("chunk_index"): c.get("text", "") for c in all_chunks}
    start = max(0, chunk_index - n)
    end = chunk_index + n
    parts = [chunk_map[i] for i in range(start, end + 1) if i in chunk_map]
    return "\n\n".join(parts) if parts else chunk.get("text", "")


def register_tools(mcp: FastMCP) -> None:
    """Register all indexer-related tools on the FastMCP instance."""

    @mcp.tool(
        name="acg_index_url",
        description="Index a URL for audited context generation. Fetches the URL, extracts text, chunks by sentences, generates SHI + CSS selectors, and stores in MongoDB for later verification.",
    )
    def acg_index_url(url: str, sentences_per_chunk: int = 8) -> str:
        """Index a URL for ACG.

        Args:
            url: The URL to index (http/https).
            sentences_per_chunk: Number of sentences per chunk (5-15, default: 8).

        Returns:
            JSON string with indexing results.
        """
        try:
            result = _index_url(url, sentences_per_chunk)
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "url": url})

    @mcp.tool(
        name="acg_check_indexed",
        description="Check if a query has matching results in indexed sources. Returns confidence score (0-1) with tier (HIGH/MEDIUM/LOW). HIGH or MEDIUM means answer from indexed sources is reliable — do NOT use web_fetch.",
    )
    def acg_check_indexed(query: str) -> str:
        """Check indexed sources for a query.

        Args:
            query: The search query to check against indexed sources.

        Returns:
            JSON with confidence score, tier, matches, and instruction.
        """
        try:
            results = _search_sources(query, limit=5)
            total = count_sources()

            if not results:
                return json.dumps({
                    "confidence": 0.0, "tier": "LOW", "answerable": False,
                    "matches": [], "coverage": {"sources_matched": 0, "total_indexed": total},
                    "instruction": "No matching sources found. Use web_fetch.",
                }, indent=2)

            confidence = _compute_confidence(results, total)
            tier = _get_confidence_tier(confidence)
            distinct = len(set(r.get("url", "") for r in results if r.get("url")))

            instruction = (
                f"{tier} confidence — answer from indexed sources. DO NOT use web_fetch."
                if confidence >= 0.4
                else "LOW confidence — weak match. Use web_fetch and cross-reference."
            )

            matches = []
            for r in results:
                expanded = _expand_chunk(r)
                matches.append({
                    "url": r.get("url", ""),
                    "shi_prefix": r.get("shi_prefix", ""),
                    "css_selector": r.get("css_selector", ""),
                    "relevance_score": r.get("score", 0),
                    "chunk_index": r.get("chunk_index"),
                    "text_preview": expanded[:1200],
                    "expanded": len(expanded) > len(r.get("text", "") or ""),
                })

            return json.dumps({
                "confidence": round(confidence, 4), "tier": tier,
                "answerable": confidence >= 0.7, "matches": matches,
                "coverage": {"sources_matched": distinct, "total_indexed": total},
                "instruction": instruction,
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "confidence": 0.0, "tier": "LOW"})

    @mcp.tool(
        name="acg_search_sources",
        description="Search indexed sources by keyword. Returns matching chunks with source metadata and relevance scores.",
    )
    def acg_search_sources(query: str, limit: int = 5) -> str:
        """Search indexed sources by keyword.

        Args:
            query: Search query string.
            limit: Maximum results (default: 5, max: 20).

        Returns:
            JSON list of matching chunks.
        """
        try:
            results = _search_sources(query, min(limit, 20))
            return json.dumps(results, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool(
        name="acg_list_sources",
        description="List all indexed sources with URL, SHI prefix, and chunk count.",
    )
    def acg_list_sources() -> str:
        """List all indexed sources."""
        try:
            sources = list_sources()
            return json.dumps(sources, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool(
        name="acg_count_sources",
        description="Count the total number of indexed sources in the database.",
    )
    def acg_count_sources() -> str:
        """Count indexed sources."""
        try:
            count = count_sources()
            return json.dumps({"total_indexed": count}, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool(
        name="acg_reset_database",
        description="DANGER: Drop all ACG collections (sources, data, claims, relationships, var_entries). This permanently deletes all indexed data. Requires confirm=true.",
    )
    def acg_reset_database(confirm: bool = False) -> str:
        """Reset the ACG database.

        Args:
            confirm: Must be true to proceed.

        Returns:
            JSON with reset results.
        """
        if not confirm:
            return json.dumps({"error": "confirm=true is required to reset the database"})
        try:
            from src.acg.db import reset_database
            result = reset_database()
            return json.dumps(result, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})
