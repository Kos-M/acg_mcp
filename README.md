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

- Python 3.11+
- MongoDB instance (local or Atlas)
  - Atlas Vector Search is **optional** — falls back to keyword search if no embedding model

## Installation

There are two ways to install — choose based on your use case:

### Option A: Local development (editable install)

For local development where you'll edit the code, install in editable mode:

```bash
# Clone the repo
git clone https://github.com/Kos-M/acg_mcp.git
cd acg_mcp

# Install globally in editable mode (recommended for agents/CLI usage)
pip install -e .

# Or use a venv:
# python -m venv venv && source venv/bin/activate && pip install -e .
```

This makes the `acg-mcp` command available **system-wide** (or venv-wide), so you can
run it from any directory.

### Option B: Using the source directly

```bash
git clone https://github.com/Kos-M/acg_mcp.git
cd acg_mcp
pip install -r requirements.txt
# Then run with: python -m src.server
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

### Run the MCP server (stdio transport)

**After editable install (recommended for global use):**
```bash
# Works from ANY directory — no venv activation needed if installed system-wide
acg-mcp
```

**Without installing the CLI (source directory only):**
```bash
cd /path/to/acg_mcp
python -m src.server
```

### Connect from an MCP client

The server communicates over **stdio**. These examples work for both
Claude Desktop and Opencode (same `mcpServers` JSON format).

#### Global install (recommended for agents & tools)

After `pip install -e .`, the `acg-mcp` command is available globally.
Use it directly in your MCP config — no path needed:

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

#### Running from source directory

If you haven't installed the CLI, use the full path:

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

> **Important:** When using `python -m src.server`, run the MCP client from
> the project root (`/path/to/acg_mcp`) or set `cwd` in the MCP config.

### Config locations

| Tool | Config File | Scope |
|------|-------------|-------|
| Claude Desktop | `claude_desktop_config.json` | User-wide |
| Opencode | `~/.opencode/mcp.json` | User-wide (global) |
| Opencode | `.opencode/mcp.json` | Per-project (local) |

## Usage from other tools & agents

Once installed globally with `pip install -e .`, any tool or agent on the
machine can use acg-mcp by referencing it in their MCP configuration.

### Example: WEBFORGE agent setup

Add to your agent's MCP config (e.g., `~/.opencode/mcp.json`):

```json
{
  "mcpServers": {
    "acg-mcp": {
      "command": "acg-mcp",
      "env": {
        "MONGO_URI": "mongodb://localhost:27017"
      }
    }
  }
}
```

The agent can then call ACG tools directly:
- `acg_check_indexed()` — Check if answers exist in indexed sources
- `acg_index_url()` — Index new URLs
- `acg_verify_claims()` — Verify grounded text claims
- `acg_search_sources()` — Search indexed knowledge base

### Passing environment variables

Pass `MONGO_URI` and other config via the `env` field in the MCP config.
The server also loads `.env` from the project directory if present.

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
