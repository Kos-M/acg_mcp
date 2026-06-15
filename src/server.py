"""ACG MCP Server — Main entry point.

Exposes ACG Protocol tools via MCP stdio transport.
Uses FastMCP for simple decorator-based tool registration.
"""

import asyncio
import logging
import sys

from mcp.server.fastmcp import FastMCP

from src.config import config
from src.tools.indexer_tools import register_tools as register_indexer
from src.tools.verifier_tools import register_tools as register_verifier
from src.tools.spider_tools import register_tools as register_spider

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

    return mcp


def main() -> None:
    """Entry point for the acg-mcp CLI command."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger.info(f"Starting {config.SERVER_NAME} v{config.SERVER_VERSION}...")
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
