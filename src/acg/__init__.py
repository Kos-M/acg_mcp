"""ACG Protocol package — ported from webforge core/acg/.

Provides verifiable, auditable context generation via UGVP + RSVP protocols.
Standalone copy — no webforge dependencies.
"""

from src.acg.shi import generate_shi, generate_shi_full, verify_shi
from src.acg.ugvp import (
    make_claim_marker,
    parse_claim_markers,
    make_ssr_entry,
    CLAIM_MARKER_PATTERN,
)
from src.acg.rsvp import (
    make_relationship_marker,
    parse_relationship_markers,
    make_rar_entry,
    RELATIONSHIP_TYPES,
    RELATIONSHIP_MARKER_PATTERN,
)
from src.acg.var import build_var, var_to_json, var_from_json
from src.acg.indexer import index_url, search_sources, fetch_url, extract_text_from_html, group_into_chunks
from src.acg.spider import extract_links, crawl_urls, crawl_and_summarize, is_same_domain
from src.acg.db import (
    save_source, get_source, get_source_by_url_hash, get_source_by_url,
    delete_source, list_sources, count_sources,
    save_data_chunks, get_chunks_by_source, search_chunks, vector_search,
    delete_chunks_by_source, embed_text, cosine_similarity,
    save_claim, get_claim, list_claims, delete_claim,
    save_relationship, get_relationship, list_relationships, delete_relationship,
    save_var_entry, list_var_entries,
    reset_database, close_connection,
)
from src.acg.verifier import verify_claims

__all__ = [
    "generate_shi", "generate_shi_full", "verify_shi",
    "make_claim_marker", "parse_claim_markers", "make_ssr_entry", "CLAIM_MARKER_PATTERN",
    "make_relationship_marker", "parse_relationship_markers", "make_rar_entry",
    "RELATIONSHIP_TYPES", "RELATIONSHIP_MARKER_PATTERN",
    "build_var", "var_to_json", "var_from_json",
    "index_url", "search_sources", "fetch_url", "extract_text_from_html", "group_into_chunks",
    "extract_links", "crawl_urls", "crawl_and_summarize", "is_same_domain",
    "save_source", "get_source", "get_source_by_url_hash", "get_source_by_url",
    "delete_source", "list_sources", "count_sources",
    "save_data_chunks", "get_chunks_by_source", "search_chunks", "vector_search",
    "delete_chunks_by_source", "embed_text", "cosine_similarity",
    "save_claim", "get_claim", "list_claims", "delete_claim",
    "save_relationship", "get_relationship", "list_relationships", "delete_relationship",
    "save_var_entry", "list_var_entries",
    "reset_database", "close_connection",
    "verify_claims",
]
