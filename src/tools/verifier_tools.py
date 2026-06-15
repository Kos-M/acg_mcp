"""MCP tools for claim verification and grounded text generation.

Uses FastMCP @mcp.tool() decorator pattern.
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

from src.acg.ugvp import make_claim_marker, parse_claim_markers, make_ssr_entry
from src.acg.rsvp import make_relationship_marker, parse_relationship_markers, make_rar_entry
from src.acg.var import build_var, var_to_json
from src.acg.verifier import verify_claims as _verify_claims
from src.acg.shi import generate_shi

logger = logging.getLogger(__name__)


def register_tools(mcp: FastMCP) -> None:
    """Register all verification-related tools on the FastMCP instance."""

    @mcp.tool(
        name="acg_generate_grounded_text",
        description="Generate text with inline Claim Markers [C1:shi:css=...] and optional Relationship Markers (R1:TYPE:C1,C2) for verifiable fact-checking using UGVP + RSVP protocols.",
    )
    def acg_generate_grounded_text(
        claim: str,
        source_url: str = "",
        shi_prefix: str = "",
        css_selector: str = "",
        rel_type: str = "",
        related_claim_ids: str = "",
        synthesis_text: str = "",
    ) -> str:
        """Generate grounded text with claim markers.

        Args:
            claim: The claim text to ground.
            source_url: Source URL (optional, for reference).
            shi_prefix: SHI prefix from indexing. If empty, auto-generated from claim.
            css_selector: CSS selector for claim location.
            rel_type: Optional relationship type (SUMMARY, CONTRADICTION, EXTENSION, CAUSAL, ANALOGY).
            related_claim_ids: Comma-separated claim IDs (e.g., "C1,C2") if relationship.
            synthesis_text: Text describing the relationship.

        Returns:
            Grounded text with inline markers.
        """
        try:
            if not shi_prefix:
                shi_prefix = generate_shi(claim)
            if not css_selector:
                css_selector = "#acg-auto"

            claim_marker = make_claim_marker(1, shi_prefix, css_selector)
            grounded = f"{claim} {claim_marker}"

            if rel_type and rel_type.upper() in (
                "SUMMARY", "CONTRADICTION", "EXTENSION", "CAUSAL", "ANALOGY"
            ):
                rel_type = rel_type.upper()
                ids = [c.strip() for c in related_claim_ids.split(",") if c.strip()] or ["C1"]
                rel_marker = make_relationship_marker(1, rel_type, ids)
                text = synthesis_text or f"Relationship: {rel_type}"
                grounded += f"\n{text} {rel_marker}"

            return grounded
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool(
        name="acg_verify_claims",
        description="Verify all claims in grounded text against their indexed sources. Parses Claim Markers, re-fetches sources, and checks if claims exist using fuzzy matching.",
    )
    def acg_verify_claims(text: str) -> str:
        """Verify claims in grounded text.

        Args:
            text: Grounded text containing Claim Markers like [C1:shi_prefix:css=selector].

        Returns:
            JSON verification report with per-claim results.
        """
        try:
            result = _verify_claims(text)
            return json.dumps(result, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool(
        name="acg_build_var",
        description="Build a Veracity Audit Registry (VAR) from grounded text. Extracts Claim Markers and Relationship Markers, then builds a complete audit trail JSON with SSR and RAR entries.",
    )
    def acg_build_var(text: str) -> str:
        """Build VAR from grounded text.

        Args:
            text: Grounded text with Claim Markers and optionally Relationship Markers.

        Returns:
            JSON Veracity Audit Registry.
        """
        try:
            claim_markers = parse_claim_markers(text)
            ssr_entries = [
                make_ssr_entry(
                    claim_id=m["claim_id"], shi_prefix=m["shi_prefix"],
                    source_url="", css_selector=m["css_selector"],
                    claim_text="", verified=False,
                )
                for m in claim_markers
            ]

            rel_markers = parse_relationship_markers(text)
            rar_entries = [
                make_rar_entry(
                    rel_id=m["rel_id"], rel_type=m["rel_type"],
                    claim_ids=m["claim_ids"], synthesis_text="",
                )
                for m in rel_markers
            ]

            var = build_var(ssr_entries, rar_entries)
            return var_to_json(var)
        except Exception as e:
            return json.dumps({"error": str(e)})
