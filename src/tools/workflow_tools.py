"""MCP tool for the enforced ACG workflow (single-call pipeline).

Exposes the full Audited Context Generation pipeline as ONE tool:
search -> (index if needed) -> ground -> verify -> audit.
Use this when you want the protocol followed end-to-end; the individual
tools remain available for custom, adapted workflows.
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

from src.acg.workflow import run_workflow, format_audit_footer

logger = logging.getLogger(__name__)


def register_tools(mcp: FastMCP) -> None:
    """Register the workflow tool on the FastMCP instance."""

    @mcp.tool(
        name="acg_run_workflow",
        description=(
            "Run the ENFORCED ACG workflow end-to-end as a single call: "
            "search indexed sources, auto-index a URL when confidence is LOW, "
            "compose a grounded answer with Claim Markers, verify every claim "
            "against its source, and build a Veracity Audit Registry. "
            "Returns an audited report with Chunk Signatures Table, "
            "[Claims Verified], [ACG Accuracy] and [ACG Signed]. "
            "Use this for a complete verifiable answer; use the individual "
            "acg_* tools when you need to adapt the protocol to your own workflow."
        ),
    )
    def acg_run_workflow(query: str, url: str = "", auto_index: bool = True) -> str:
        """Run the enforced ACG pipeline.

        Args:
            query: The question to answer from indexed sources.
            url: Optional URL to index first when confidence is LOW.
            auto_index: Whether to index the URL automatically on LOW confidence.

        Returns:
            JSON audited report (answer + verification + audit trail).
        """
        try:
            report = run_workflow(query, url=url, auto_index=auto_index)
            return json.dumps(report, indent=2, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "query": query})
