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

Requires **Python 3.11+**. A virtual environment is **strongly recommended** —
on recent Debian/Ubuntu (23.04+) and other PEP 668 distros, bare `pip install`
refuses to write to the system Python, so Option A is the reliable path there.

### Option A: Virtual environment + editable install (recommended)

```bash
git clone https://github.com/Kos-M/acg_mcp.git
cd acg_mcp

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -e .
```

This installs the package and its dependencies into the venv and puts the
`acg-mcp` command on PATH **while the venv is active**. Editable mode means
local code changes apply immediately — no reinstall needed.

MCP clients don't source your shell, so point them at the venv's binary
by absolute path instead of relying on PATH (see [Connect from an MCP client](#connect-from-an-mcp-client)).

### Option B: System-wide install (agents / CLI tools)

If you want `acg-mcp` available on PATH from **any** directory without a venv:

```bash
git clone https://github.com/Kos-M/acg_mcp.git
cd acg_mcp
pip install -e .
```

If pip fails with `externally-managed-environment` (PEP 668), either use a venv
(Option A) or add `--break-system-packages`.

### Option C: Run from source (no install)

```bash
git clone https://github.com/Kos-M/acg_mcp.git
cd acg_mcp
pip install -r requirements.txt
# Must be run from the project root:
python -m src.server
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

# Vector search candidate cap (optional, default: 10000).
# Number of embedded chunks scanned per query. Raise it if your index
# exceeds this and you see false "LOW confidence" results.
ACG_VECTOR_MAX_CANDIDATES=10000
```

For MongoDB Atlas:
```env
MONGO_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/acg_protocol?retryWrites=true&w=majority
```

## Usage

### Run the MCP server (stdio transport)

**After installing with Option A or B:**
```bash
# venv (Option A): works while the venv is active
# system-wide (Option B): works from any directory
acg-mcp
```

**Without installing the CLI (source directory only):**
```bash
cd /path/to/acg_mcp
python -m src.server
```

### Connect from an MCP client

The server communicates over **stdio**. Claude Desktop and Opencode use
**different** config formats, so the examples below are split per client:
Claude Desktop uses the `mcpServers` key; Opencode uses a top-level `mcp`
key where every server needs `"type"` and `command` is an array.

#### Claude Desktop

Claude Desktop reads `claude_desktop_config.json` and uses the `mcpServers`
key. If you installed with **Option A** (venv), point at the venv binary —
clients don't source your shell:

```json
{
  "mcpServers": {
    "acg-mcp": {
      "command": "/absolute/path/to/acg_mcp/venv/bin/acg-mcp",
      "env": {
        "MONGO_URI": "mongodb+srv://..."
      }
    }
  }
}
```

With a system-wide install (Option B), the bare command works directly:

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

#### Opencode

Opencode reads `opencode.json` (or `opencode.jsonc`) and uses a top-level
`mcp` key. Local servers require `"type": "local"`, `command` as an **array**
of the binary + args, and env vars under `"environment"` (not `"env"`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "acg-mcp": {
      "type": "local",
      "command": ["/absolute/path/to/acg_mcp/venv/bin/acg-mcp"],
      "enabled": true,
      "environment": {
        "MONGO_URI": "mongodb+srv://..."
      }
    }
  }
}
```

With a system-wide install (Option B), use the bare command:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "acg-mcp": {
      "type": "local",
      "command": ["acg-mcp"],
      "enabled": true,
      "environment": {
        "MONGO_URI": "mongodb+srv://..."
      }
    }
  }
}
```

#### Running from source directory

If you haven't installed the CLI, use the full path. Claude Desktop:

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

Opencode — note `cwd` so `src.server` resolves relative to the project:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "acg-mcp": {
      "type": "local",
      "command": ["python", "-m", "src.server"],
      "cwd": "/path/to/acg_mcp",
      "environment": {
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
| Opencode | `~/.config/opencode/opencode.json` | User-wide (global) |
| Opencode | `opencode.json` / `opencode.jsonc` (project root) | Per-project (local) |

## Usage from other tools & agents

Once installed with **Option A** (venv) or **Option B** (system-wide), any tool or
agent on the machine can use acg-mcp by referencing it in their MCP configuration.

### Example: WEBFORGE agent setup

Add to the agent's global Opencode config (`~/.config/opencode/opencode.json`)
using Opencode's `mcp` syntax:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "acg-mcp": {
      "type": "local",
      "command": ["acg-mcp"],
      "enabled": true,
      "environment": {
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

Pass `MONGO_URI` and other config via the `env` field (Claude Desktop) or
`environment` field (Opencode) in the MCP config. The server also loads
`.env` from the project directory (via python-dotenv) when installed
editable (`pip install -e .`) or run from the project root.

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
