"""Configuration for ACG MCP server.

Reads from environment variables with sensible defaults.
Loads .env file automatically via python-dotenv.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """ACG MCP configuration."""

    # MongoDB connection
    MONGO_URI: str = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB: str = os.environ.get("MONGO_DB", "acg_protocol")

    # Embedding model cache directory
    EMBEDDING_CACHE_DIR: str | None = os.environ.get("EMBEDDING_CACHE_DIR", None)

    # Server name/version
    SERVER_NAME: str = "acg-mcp"
    SERVER_VERSION: str = "0.1.0"
    SERVER_DESCRIPTION: str = (
        "ACG Protocol MCP Server — Index URLs, search sources, "
        "verify claims with grounded fact-checking via MongoDB."
    )


config = Config()
