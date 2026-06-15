# ACG MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Standalone MCP server** for the [Audited Context Generation (ACG) Protocol](https://github.com/Kos-M/acg_protocol) — verifiable fact-checking and grounded RAG via MongoDB.

ACG provides a dual-layer standard for veracity assurance:
- **UGVP (Layer 1)**: Atomic fact grounding with Claim Markers and Source Hash Identity (SHI)
- **RSVP (Layer 2)**: Logical synthesis verification with Relationship Markers

## Features

- **Index URLs** → Extract text, chunk by sentences, generate embeddings, store in MongoDB
- **Search Sources** → Semantic (vector) + keyword search across indexed content
- **Check Indexed** → Confidence-scored lookup to avoid unnecessary web_fetch calls
- **Generate Grounded Text** → Create verifiable output with inline Claim Markers
- **Verify Claims** → Re-fetch sources, fuzzy-match claims against source text
- **Build VAR** → Generate machine-readable Veracity Audit Registry (SSR + RAR)
- **Crawl & Index** → BFS URL discovery + automatic ACG indexing pipeline
- **Reset Database** → Drop all ACG collections (with confirmation guard)

## Requirements

- Python 3.12+
- MongoDB instance (local or Atlas)
  - Atlas Vector Search is **optional** — falls back to keyword search if no embedding model

## Installation

```bash
# Clone the repo
git clone https://github.com/Kos-M/acg_mcp.git
cd acg_mcp

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Copy `.env.sample` to `.env` and configure:

```env
# MongoDB connection string (required)
MONGO_URI=mongodb://localhost:27017

# MongoDB database name (optional, default: acg_protocol)
MONGO_DB=acg_protocol

# Embedding model cache directory (optional)
EMBEDDING_CACHE_DIR=
```

For MongoDB Atlas:
```env
MONGO_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/acg_protocol?retryWrites=true&w=majority
```

## Usage

### Start the MCP server (stdio transport)

```bash
python -m src.server
```

Or use the installed CLI:

```bash
pip install -e .
acg-mcp
```

### Connect from an MCP client

The server communicates over **stdio**.

#### Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "acg-mcp": {
      "command": "python",
      "args": ["-m", "src.server"],
      "env": {
        "MONGO_URI": "mongodb+srv://..."
      }
    }
  }
}
```

#### Opencode

Add to your `~/.opencode/mcp.json` (or project-local `.opencode/mcp.json`):

```json
{
  "mcpServers": {
    "acg-mcp": {
      "command": "python",
      "args": ["-m", "src.server"],
      "env": {
        "MONGO_URI": "mongodb+srv://..."
      }
    }
  }
}
```

If you have the package installed, you can use the CLI directly:

```json
{
  "mcpServers": {
    "acg-mcp": {
      "command": "acg-mcp",
      "env": {
        "MONGO_URI": "mongodb+srv://..."
      }
    }
  }
}
```

> **Tip:** Opencode supports the same `mcpServers` JSON format as Claude Desktop. Place the config in `~/.opencode/mcp.json` for global access, or in `.opencode/mcp.json` at your project root for per-project configuration.
```

## Available Tools

| Tool | Description |
|------|-------------|
| `acg_index_url` | Index a URL for ACG — fetches, chunks, embeds, stores |
| `acg_check_indexed` | Check if a query has results in indexed sources |
| `acg_search_sources` | Search indexed sources by keyword |
| `acg_list_sources` | List all indexed sources |
| `acg_count_sources` | Count total indexed sources |
| `acg_generate_grounded_text` | Create text with Claim Markers (UGVP) |
| `acg_verify_claims` | Verify claims against their sources (fuzzy matching) |
| `acg_build_var` | Build Veracity Audit Registry (SSR + RAR) |
| `acg_crawl_and_index` | Crawl + index multiple URLs (background support) |
| `acg_crawl_status` | Check background crawl task status |
| `acg_crawl_list_tasks` | List all background crawl tasks |
| `acg_reset_database` | ⚠️ Delete all indexed data (requires confirm=true) |

## Database Collections

The server uses a standard MongoDB collection structure:

| Collection | Purpose |
|------------|---------|
| `sources` | Source metadata (url, shi_prefix, url_hash, total_chunks) |
| `data` | Chunks with embeddings (source_id, text, sentences, embedding) |
| `claims` | Verified claims (claim_id, shi_prefix, claim_text, verified) |
| `relationships` | RSVP relationship records (rel_id, rel_type, claim_ids) |
| `var_entries` | Veracity Audit Registry entries |

Indexes are auto-created on first connection.

## License

MIT
