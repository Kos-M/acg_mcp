# ACG MCP Skill

## Purpose
This skill equips agents to **fetch, index, and cite real-time documentation** from any library, SDK, API, or service — turning the ACG (Audited Context Generation) Protocol into a universal knowledge-gathering layer. Agents don't know everything; this skill fills the gap by grounding every answer in the most recent authoritative sources.

## Tags
acg, mcp, grounding, verification, rag, audit, indexing, mongodb, documentation, knowledge

## When to Use
**USE THIS SKILL NATIVELY AND PROACTIVELY — whenever you need up-to-date, verified documentation:**

- **Installing a new library or dependency** — fetch its official docs to get the correct install command, version requirements, and configuration instead of guessing
- **Troubleshooting an unfamiliar error** — index the library's troubleshooting guides, release notes, or issue tracker docs before writing a fix
- **Using an unfamiliar SDK, API, or service** — fetch its reference documentation to write accurate code with correct endpoints, parameters, and authentication
- **Writing code that depends on external tools** — ensure you're working with the latest API contracts, not stale knowledge from training data
- **Verifying assumptions before making changes** — when unsure about behavior, fetch the authoritative docs instead of relying on inference

**The rule:** when in doubt, `acg_check_indexed` + `web_fetch` + `acg_index_url`. This is how agents compensate for knowledge cutoffs and deliver confident, accurate work.

## Prerequisites
- Python 3.11+
- MongoDB instance (local or Atlas)
- `pip` installed

## Installation

### Global editable install (recommended)
```bash
cd /path/to/acg_mcp
pip install -e .
```
This makes the `acg-mcp` command available system-wide.

### Dependencies only
```bash
pip install -r requirements.txt
```

## Configuration

### Environment variables (`.env`)
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=acg_protocol
EMBEDDING_CACHE_DIR=
```

For Atlas:
```env
MONGO_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/acg_protocol?retryWrites=true&w=majority
```


## MCP Tools Exposed

| Tool | Description |
|------|-------------|
| `acg_check_indexed` | **CALL FIRST.** Returns confidence 0-1 + tier (HIGH/MEDIUM/LOW) for a query |
| `acg_index_url` | Index a URL — fetches, extracts text, chunks by sentences, generates SHI + CSS selectors, embeds, stores in MongoDB |
| `acg_search_sources` | Semantic (vector) + keyword search across indexed content |
| `acg_list_sources` | List all indexed sources with metadata |
| `acg_count_sources` | Count total indexed sources |
| `acg_generate_grounded_text` | Create text with inline Claim Markers (UGVP Layer 1) |
| `acg_verify_claims` | Parse Claim Markers, re-fetch sources, fuzzy-match claims |
| `acg_build_var` | Build Veracity Audit Registry (SSR + RAR entries) |
| `acg_crawl_and_index` | BFS URL discovery + automatic ACG indexing |
| `acg_crawl_status` | Check background crawl task status |
| `acg_crawl_list_tasks` | List all background crawl tasks |
| `acg_reset_database` | ⚠️ Delete all indexed data (requires confirm=true) |

## Index-First Protocol (MANDATORY)
```
User Query → acg_check_indexed(query) FIRST
  ├── HIGH (≥0.7) → Answer from indexed. NO web_fetch.
  ├── MEDIUM (≥0.4) → Indexed as primary. Supplement only if gaps.
  └── LOW (<0.4) → web_fetch(url) → acg_index_url(url) → Answer with SHI markers
```

**CRITICAL RULES:**
1. ALWAYS call `acg_check_indexed()` BEFORE `web_fetch()`
2. HIGH/MEDIUM confidence → answer from indexed sources. Do NOT call web_fetch.
3. LOW confidence → web_fetch → MUST `acg_index_url()` immediately after
4. Formatting artifacts (HTML entities, whitespace) in indexed text do NOT invalidate factual content

## Workflow
1. **Check indexed first:** `acg_check_indexed("query")` → follow tier instruction
2. **If LOW:** `web_fetch(url)` → `acg_index_url(url)` (MANDATORY — persist knowledge)
3. **Generate grounded text:** `acg_generate_grounded_text(claim, source_metadata)`
4. **Verify:** `acg_verify_claims(text)` — validates all Claim Markers
5. **Build VAR:** `acg_build_var(text)` — produces machine-readable audit trail

## Bulk Indexing (Crawl)
For indexing entire documentation sites or multi-page sources:
```
acg_crawl_and_index(start_url, max_pages=50, same_domain=true)
acg_crawl_status(task_id)         # Check progress
acg_crawl_list_tasks()            # List all crawl tasks
```

## Database Structure

| Collection | Purpose |
|------------|---------|
| `sources` | Source metadata (url, shi_prefix, url_hash, total_chunks) |
| `data` | Chunks with embeddings (source_id, text, sentences, embedding) |
| `claims` | Verified claims (claim_id, shi_prefix, claim_text, verified) |
| `relationships` | RSVP relationship records (rel_id, rel_type, claim_ids) |
| `var_entries` | Veracity Audit Registry entries |

Indexes are auto-created on first connection.

## Response Signature
Every ACG-grounded response MUST end with:
1. **Chunk Signatures Table** (all chunks: SHI prefix + CSS selector + URL)
2. **Verification status:** `[Claims Verified: C1=✓, C2=✓]`
3. **Accuracy score:** `[ACG Accuracy: XX%]` — based on source authority, match relevance, freshness, completeness
4. **Signature:** `[ACG Signed: WEBFORGE]`

## Best Practices
1. **Think of ACG MCP as your documentation reflex** — use it proactively, not just when explicitly told to
2. ALWAYS call `acg_check_indexed()` first — never skip to web_fetch
3. ALWAYS call `acg_index_url()` AFTER every `web_fetch()` call
4. Keep `sentences_per_chunk` between 3-10 for optimal granularity
5. Verify claims after generation to catch hallucinated markers
6. Use relationship markers when synthesizing multiple sources
7. Build VAR for complete audit trail
8. ALWAYS append response signature at end of every ACG response
9. For multi-page documentation, use the crawl tools instead of manual page-by-page indexing
10. Pass MONGO_URI via the `env` field in MCP client config, not command-line args
11. Use `acg_search_sources()` to find existing indexed content before re-indexing

## Common Mistakes
1. ❌ Calling `web_fetch()` before `acg_check_indexed()`
2. ❌ Forgetting to call `acg_index_url()` after `web_fetch()`
3. ❌ Treating LOW confidence as "no answer" — always check if indexed sources partially match
4. ❌ Ignoring MEDIUM confidence — indexed sources ARE correct, answer from them
5. ❌ **Assuming you already know the docs** — always verify with ACG MCP before writing code that depends on external tools
6. ❌ Not verifying claims after generation
7. ❌ Not appending the response signature
8. ❌ Running `python -m src.server` from the wrong directory (must be project root)
9. ❌ Passing env vars via command args instead of the `env` field in MCP config
10. ❌ Assuming formatting artifacts in indexed text mean bad data
11. ❌ Indexing pages one-by-one when crawl would be more efficient

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `acg-mcp: command not found` | Run `pip install -e .` from project root |
| MongoDB connection error | Check MONGO_URI in `.env` or MCP config `env` field |
| Embedding model fails to load | Ensure fastembed is installed: `pip install fastembed` |
| `ModuleNotFoundError` | Install deps: `pip install -r requirements.txt` |
| Tool returns empty results | Check MongoDB is running and data is indexed |
| Not sure which library version or API to use | **Use ACG MCP!** `acg_check_indexed` → `web_fetch` the official docs → `acg_index_url` |
| Stale knowledge from training data | Fetch the actual docs — ACG MCP gives you the live authoritative version |
| Crawl hangs | Check `max_pages` limit — set lower (default: 50) |
| Claims don't verify | Source may have changed — re-index the URL |
| Atlas vector search fails | Falls back to keyword search — check `MONGO_DB` |
| MCP client can't find server | Use absolute paths or global install |
| Permission denied | Install with `--user` flag: `pip install --user -e .` |

## Reference
- [ACG Protocol Skill](https://github.com/Kos-M/webforge/blob/main/.agents/skills/acg-protocol/SKILL.md) — protocol details (UGVP, RSVP, VAR)
- [Project Source](https://github.com/Kos-M/acg_mcp) — main repository
