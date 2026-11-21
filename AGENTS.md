# ACG MCP

Standalone MCP server for the ACG (Audited Context Generation) Protocol.

## Directory Structure
```
├── AGENTS.md
├── README.md
├── SKILL.md          # Agent skill file (standard format for WEBFORGE integration)
├── pyproject.toml
├── requirements.txt
├── .env.sample
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── server.py          # FastMCP server entry point
│   ├── config.py          # MongoDB env var config
│   ├── acg/               # Core ACG protocol library (ported from webforge)
│   │   ├── __init__.py
│   │   ├── shi.py         # Source Hash Identity
│   │   ├── ugvp.py        # Claim Markers (Layer 1)
│   │   ├── rsvp.py        # Relationship Markers (Layer 2)
│   │   ├── var.py         # Veracity Audit Registry
│   │   ├── db.py          # MongoDB CRUD operations
│   │   ├── corpus.py      # In-process corpus cache (chunk text + embedding matrix) used by search
│   │   ├── confidence.py  # Confidence tier / answerable / instruction (single source of truth)
│   │   ├── indexer.py     # URL fetching, main-content extraction, chunking, embedding
│   │   ├── verifier.py    # Claim verification (chunk-scoped, number-strict)
│   │   └── spider.py      # BFS URL crawler
│   └── tools/             # MCP tool registrations
│       ├── __init__.py
│       ├── indexer_tools.py
│       ├── verifier_tools.py
│       └── spider_tools.py
├── tests/
│   └── __init__.py
└── .github/
    └── workflows/
        └── test.yml
```

## Stack
Python 3.12+, MCP Python SDK, pymongo, requests, beautifulsoup4, lxml, fastembed, numpy

## Commands
- Run (global CLI): `acg-mcp` (after `pip install -e .`)
- Run (source): `python -m src.server` (from project root only)
- Install (global editable): `pip install -e .`
- Install (deps only): `pip install -r requirements.txt`
- Test: `pytest tests/ -v`

## Env
- `MONGO_URI`: MongoDB connection string (required)
- `MONGO_DB`: Database name (default: acg_protocol)
- `EMBEDDING_CACHE_DIR`: Optional cache directory for embedding model
- `ACG_CACHE_DIR`: Optional directory for the on-disk corpus cache (default: `~/.cache/acg-mcp`; empty disables it)

## Notes
- Tools are plain `def` functions; `run_sync_tools_in_threads` (src/tools/__init__.py) registers them to run in worker threads so one slow call never blocks the server.
- Search never scans MongoDB per query: `corpus.py` mirrors the chunks in memory and re-syncs only changed sources. Keep it that way — the DB may be remote (Atlas).
- Changing `extract_text_from_html` output requires bumping `EXTRACTOR_VERSION` (indexer.py). The verifier re-extracts each source with the version it was indexed with, otherwise SHI checks fail for existing sources.
