"""ACG MCP Server — Main entry point.

Exposes ACG Protocol tools via MCP stdio transport.
Uses FastMCP for simple decorator-based tool registration.

Also ships a one-shot CLI for the ENFORCED workflow:

    acg-mcp --workflow "your question" [url-to-index-if-low-confidence]
"""

import asyncio
import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.config import config
from src.tools.indexer_tools import register_tools as register_indexer
from src.tools.verifier_tools import register_tools as register_verifier
from src.tools.spider_tools import register_tools as register_spider
from src.tools.workflow_tools import register_tools as register_workflow

logger = logging.getLogger(__name__)


def create_server() -> FastMCP:
    """Create and configure the ACG MCP server with all tools."""
    mcp = FastMCP(
        name=config.SERVER_NAME,
        instructions=config.SERVER_DESCRIPTION,
        debug=False,
    )

    # Register all tool groups
    register_indexer(mcp)
    register_verifier(mcp)
    register_spider(mcp)
    register_workflow(mcp)

    return mcp


def main() -> None:
    """Entry point for the acg-mcp CLI command.

    Default: run the MCP server over stdio.
    With --workflow: run the enforced ACG pipeline once and print the
    audited report (grounded answer + Chunk Signatures Table + footer).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args = sys.argv[1:]
    if args and args[0] == "--workflow":
        if len(args) < 2:
            print("usage: acg-mcp --workflow \"<query>\" [url-to-index]")
            sys.exit(2)
        query = args[1]
        url = args[2] if len(args) > 2 else ""
        from src.acg.workflow import run_workflow, format_audit_footer
        logger.info(f"Running enforced ACG workflow for: {query}")
        report = run_workflow(query, url=url)
        print(report.get("grounded_answer") or "No grounded answer (LOW confidence).")
        print()
        print(format_audit_footer(report))
        return

    logger.info(f"Starting {config.SERVER_NAME} v{config.SERVER_VERSION}...")
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
