# ACG MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Standalone MCP server** for the [Audited Context Generation (ACG) Protocol](https://github.com/Kos-M/acg_protocol) — verifiable fact-checking and grounded RAG via MongoDB.

ACG provides a dual-layer standard for veracity assurance:
- **UGVP (Layer 1)**: Atomic fact grounding with Claim Markers and Source Hash Identity (SHI)
- **RSVP (Layer 2)**: Logical synthesis verification with Relationship Markers

## Why ACG — what you can do with it

LLMs confidently state things that are wrong, and there is usually **no way to
check** — the answer is a black box with no provenance. ACG fixes this by making
every answer **auditable by construction**:

- **Ground every fact to its source.** Index a URL once and every later answer
  built from it carries inline Claim Markers like
  `[C1:9f7a2c4d8e1b:css=#acg-chunk-aa-0]` — the SHA-256-based SHI prefix
  fingerprints the exact source document, and the CSS selector points to the
  precise chunk inside it.

- **Verify instead of trust.** `acg_verify_claims` re-fetches every source and
  fuzzy-matches each claim against the actual text, so verification is not a
  self-reported LLM opinion — it is an independent, repeatable check. A claim
  either exists in the cited source or it fails.

- **Know when the knowledge base is enough.** `acg_check_indexed` returns a
  confidence score (HIGH / MEDIUM / LOW) before you ever hit the network, so
  you only fetch new pages when the index genuinely can't answer.

- **Get a machine-readable audit trail.** `acg_build_var` emits a Veracity
  Audit Registry (SSR + RAR entries) — a JSON record of every claim, its
  source fingerprint, and every logical relationship between claims, ready to
  be consumed by downstream systems or humans.

- **Use it in two modes.** Run the **enforced workflow** (`acg_run_workflow`)
  and get a complete, verified, audited answer in one call — or compose the
  individual tools any way your own workflow requires (see
  [Two ways to use ACG](#two-ways-to-use-acg)).

In short: ACG turns "trust me, the model said so" into
**"here is the claim, here is the exact source location, here is the
verification result, and here is the audit record."**

## Features

- **Enforced workflow** → One call runs the whole pipeline: search, auto-index,
  ground, verify, audit (see [Two ways to use ACG](#two-ways-to-use-acg))
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

### Run the enforced workflow from the CLI

The one-shot CLI runs the entire audited pipeline without an MCP client:

```bash
# Query the index, print the grounded answer + audit footer
acg-mcp --workflow "What does the README say about MONGO_URI?"

# Same, but auto-index a URL first when confidence is LOW
acg-mcp --workflow "How do I configure MongoDB Atlas?" https://example.com/docs/setup
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

## Two ways to use ACG

ACG ships **both** an enforced end-to-end workflow and the individual tools
it is built from. Use whichever fits your task.

### 1. Enforced workflow — the whole protocol in one call

Call `acg_run_workflow(query, url="")` and the server runs the full
pipeline for you, in this order:

1. **search** — search the indexed sources for the query
2. **index** — if confidence is LOW and a `url` was provided, index it
   first, then re-search (auto-fetch)
3. **ground** — compose a grounded answer with inline UGVP Claim Markers
4. **verify** — re-fetch every cited source and fuzzy-match each claim
5. **audit** — build the Veracity Audit Registry (SSR + RAR)

The single returned report contains everything: the grounded answer,
per-claim verification results, a **Chunk Signatures Table**, and the
audit footer — `[Claims Verified: x/y]`, `[ACG Accuracy: N%]`,
`[ACG Signed: ACG Protocol]`. You get a verifiable answer without
orchestrating any of the steps yourself.

```jsonc
// acg_run_workflow("What is the pricing of the flash model?")
{
  "query": "What is the pricing of the flash model?",
  "workflow": ["search", "ground", "verify", "audit"],
  "confidence_tier": "HIGH",
  "grounded_answer": "Flash input tokens cost $0.14 per 1M [C1:9f7a2c4d8e1b:css=#acg-chunk-aa-0].",
  "claims_verified": "1/1",
  "acg_accuracy": 100.0,
  "acg_signed": "ACG Protocol",
  "var": { "protocol": "ACG/1.0", "ssr_entries": [ /* ... */ ], "rar_entries": [] }
}
```

### 2. Individual tools — adapt ACG to your own workflow

Every step is also available as a standalone tool, so you can compose
exactly the pipeline your workflow needs — different chunking, custom
verification thresholds, your own retrieval strategy, or ACG used purely
as a post-generation audit layer.

| Tool | When to use it |
|------|----------------|
| `acg_index_url` | You have a URL and want it in the knowledge base |
| `acg_check_indexed` | You want to know if the index can answer before fetching anything |
| `acg_search_sources` | You want raw matching chunks with scores, to build your own answer |
| `acg_generate_grounded_text` | You have an answer and want to attach Claim Markers to it |
| `acg_verify_claims` | You have marked text and want an independent verification pass |
| `acg_build_var` | You want the machine-readable audit record (SSR + RAR) |
| `acg_crawl_and_index` | You have a docs site and want it indexed as a whole |

For example, a "verify-only" workflow that audits text generated elsewhere:

```text
acg_generate_grounded_text(claim, shi_prefix, css_selector)
    -> acg_verify_claims(grounded_text)
    -> acg_build_var(grounded_text)
```

## Usage from other tools & agents

Once installed with **Option A** (venv) or **Option B** (system-wide), any tool or
agent on the machine can use acg-mcp by referencing it in their MCP configuration.
Add it to the agent's global Opencode config
(`~/.config/opencode/opencode.json`) using Opencode's `mcp` syntax:

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
- `acg_run_workflow()` — One call: full verified, audited answer
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
| `acg_run_workflow` | **Enforced pipeline** — search, auto-index, ground, verify, audit in one call |
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
