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
│   │   ├── indexer.py     # URL fetching, text extraction, chunking, embedding
│   │   ├── verifier.py    # Claim verification with fuzzy matching
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
Python 3.12+, MCP Python SDK, pymongo, requests, beautifulsoup4, lxml, fastembed

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
