"""MCP tools for URL indexing and source searching.

Uses FastMCP @mcp.tool() decorator pattern.
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

from src.acg.indexer import index_url as _index_url
from src.acg.indexer import search_sources as _search_sources
from src.acg.db import list_sources, count_sources, get_neighbour_chunks
from src.acg.confidence import assess, INSTRUCTIONS

logger = logging.getLogger(__name__)


def _expand_chunk(chunk: dict, n: int = 2) -> str:
    """Return the chunk text with up to ``n`` neighbouring chunks on each side."""
    url = chunk.get("url", "")
    chunk_index = chunk.get("chunk_index")
    if not url or chunk_index is None:
        return chunk.get("text", "")
    parts = [c.get("text", "") for c in get_neighbour_chunks(url, chunk_index, n)]
    parts = [p for p in parts if p]
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
        description=(
            "Check if indexed sources can answer a query. Returns confidence (0-1), "
            "tier (HIGH/MEDIUM/LOW), answerable, the best matches and an instruction. "
            "HIGH: answer from indexed sources, no web_fetch. MEDIUM: sources cover "
            "part of the query; answer what they support and web_fetch the gaps. "
            "LOW (answerable=false): matches are likely off-topic; use web_fetch."
        ),
    )
    def acg_check_indexed(query: str) -> str:
        """Check indexed sources for a query.

        Args:
            query: The search query to check against indexed sources.

        Returns:
            JSON with confidence score, tier, answerable, matches, and instruction.
        """
        try:
            results = _search_sources(query, limit=5)
            total = count_sources()

            if not results:
                return json.dumps({
                    "confidence": 0.0, "tier": "LOW", "answerable": False,
                    "matches": [], "coverage": {"sources_matched": 0, "total_indexed": total},
                    "instruction": INSTRUCTIONS["LOW"],
                }, indent=2)

            verdict = assess(results)
            distinct = len(set(r.get("url", "") for r in results if r.get("url")))

            matches = []
            for r in results:
                expanded = _expand_chunk(r)
                matches.append({
                    "url": r.get("url", ""),
                    "shi_prefix": r.get("shi_prefix", ""),
                    "css_selector": r.get("css_selector", ""),
                    "relevance_score": r.get("hybrid_score", r.get("score", 0)),
                    "vector_score": r.get("vector_score"),
                    "keyword_coverage": r.get("keyword_coverage"),
                    "chunk_index": r.get("chunk_index"),
                    "text_preview": expanded[:1200],
                    "expanded": len(expanded) > len(r.get("text", "") or ""),
                })

            return json.dumps({
                "confidence": verdict["confidence"], "tier": verdict["tier"],
                "answerable": verdict["answerable"], "matches": matches,
                "coverage": {"sources_matched": distinct, "total_indexed": total},
                "instruction": verdict["instruction"],
            }, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "confidence": 0.0, "tier": "LOW", "answerable": False})

    @mcp.tool(
        name="acg_search_sources",
        description="Search indexed sources with vector and keyword search combined into one ranking. Returns matching chunks with source metadata, score (0-1), hybrid_score, vector_score and keyword_score.",
    )
    def acg_search_sources(query: str, limit: int = 5) -> str:
        """Search indexed sources (hybrid vector + keyword).

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
