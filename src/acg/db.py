"""MongoDB connection and CRUD operations for the ACG Protocol.

Uses a unified collection design matching the reference implementation:
- sources: Source documents (url, shi_prefix, url_hash, full_text_length, indexed_at)
- data: Chunks with embeddings (source_id, chunk_index, text, sentences, css_selector, embedding)
- claims: Claim markers (claim_id, shi_prefix, source_url, css_selector, claim_text, verified)
- relationships: RSVP relationships (rel_id, rel_type, claim_ids, synthesis_text)
- var_entries: Veracity Audit Registry entries (protocol, generated_at, ssr_entries, rar_entries)

Vector search is performed in Python (cosine similarity) since MongoDB 4.4
does not support Atlas $vectorSearch. Falls back to $regex keyword search
when no embedding model is available.
"""

import os
import re
import math
import datetime
from typing import Optional

from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError


# Default MongoDB URI - override with MONGO_URI env var
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "acg_protocol")

_client = None
_db = None

# Lazy-loaded embedding model
_embedding_model = None


def get_client() -> MongoClient:
    """Get or create the MongoDB client singleton.

    Returns:
        MongoClient instance.

    Raises:
        ConnectionFailure: If MongoDB is unreachable.
    """
    global _client
    if _client is None:
        try:
            _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            _client.admin.command("ping")
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            raise ConnectionFailure(
                f"Cannot connect to MongoDB at {MONGO_URI}: {e}"
            )
    return _client


def get_db():
    """Get or create the database singleton with all collections.

    Ensures indexes exist on first call.

    Returns:
        MongoDB database object.
    """
    global _db
    if _db is None:
        client = get_client()
        _db = client[DB_NAME]

        # --- sources collection ---
        _db.sources.create_index([("shi_prefix", ASCENDING)], unique=True)
        _db.sources.create_index([("url", ASCENDING)], unique=True)
        _db.sources.create_index([("url_hash", ASCENDING)], unique=True)

        # --- data collection (chunks + embeddings) ---
        _db.data.create_index([("source_id", ASCENDING), ("chunk_index", ASCENDING)], unique=True)
        _db.data.create_index([("text", ASCENDING)])
        _db.data.create_index([("shi_prefix", ASCENDING)])

        # --- claims collection ---
        _db.claims.create_index([("claim_id", ASCENDING)], unique=True)
        _db.claims.create_index([("shi_prefix", ASCENDING)])

        # --- relationships collection ---
        _db.relationships.create_index([("rel_id", ASCENDING)], unique=True)

        # --- var_entries collection ---
        _db.var_entries.create_index([("generated_at", ASCENDING)])

    return _db


def close_connection() -> None:
    """Close the MongoDB connection."""
    global _client, _db, _embedding_model
    if _client is not None:
        _client.close()
    _client = None
    _db = None
    _embedding_model = None


# ---------------------------------------------------------------------------
# EMBEDDING MODEL (lazy-loaded)
# ---------------------------------------------------------------------------

def get_embedding_model():
    """Get or create the embedding model singleton.

    Uses fastembed with BAAI/bge-small-en-v1.5 (384-dim, ~33MB).
    Falls back to None if fastembed is not installed.

    Returns:
        TextEmbedding instance or None.
    """
    global _embedding_model
    if _embedding_model is None:
        try:
            from fastembed import TextEmbedding
            _embedding_model = TextEmbedding(
                "BAAI/bge-small-en-v1.5",
                cache_dir=os.environ.get("EMBEDDING_CACHE_DIR", None),
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"Failed to load embedding model: {e}. "
                "Vector search will fall back to keyword search."
            )
            _embedding_model = None
    return _embedding_model


def embed_text(text: str) -> list[float]:
    """Generate an embedding vector for a text string.

    Args:
        text: Text to embed.

    Returns:
        List of floats (384-dim embedding), or empty list if model unavailable.
    """
    model = get_embedding_model()
    if model is None:
        return []
    try:
        # embed() returns generator of numpy arrays
        embeddings = list(model.embed(text))
        if embeddings:
            return embeddings[0].tolist()
        return []
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Embedding failed: {e}")
        return []


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity score (0-1).
    """
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# SOURCES
# ---------------------------------------------------------------------------

def save_source(url_hash: str, source_data: dict) -> bool:
    """Insert or update a source entry in the sources collection.

    Args:
        url_hash: MD5 hash of the URL (first 8 chars).
        source_data: Dict with keys: url, shi_prefix, total_chunks, full_text_length.

    Returns:
        True if successful.
    """
    db = get_db()
    source_data["url_hash"] = url_hash
    source_data["indexed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.sources.update_one(
        {"url_hash": url_hash},
        {"$set": source_data},
        upsert=True,
    )
    return True


def get_source(shi_prefix: str) -> Optional[dict]:
    """Find a source entry by its SHI prefix.

    Args:
        shi_prefix: The SHI prefix to search for.

    Returns:
        Source entry dict or None.
    """
    db = get_db()
    return db.sources.find_one({"shi_prefix": shi_prefix}, {"_id": False})


def get_source_by_url_hash(url_hash: str) -> Optional[dict]:
    """Get a source entry by its URL hash.

    Args:
        url_hash: MD5 hash of the URL.

    Returns:
        Source entry dict or None.
    """
    db = get_db()
    return db.sources.find_one({"url_hash": url_hash}, {"_id": False})


def get_source_by_url(url: str) -> Optional[dict]:
    """Get a source entry by its URL.

    Args:
        url: The source URL.

    Returns:
        Source entry dict or None.
    """
    db = get_db()
    return db.sources.find_one({"url": url}, {"_id": False})


def delete_source(url_hash: str) -> bool:
    """Delete a source entry and all its associated data chunks.

    Args:
        url_hash: MD5 hash of the URL.

    Returns:
        True if deleted, False if not found.
    """
    db = get_db()
    source = db.sources.find_one({"url_hash": url_hash})
    if not source:
        return False
    db.sources.delete_one({"url_hash": url_hash})
    db.data.delete_many({"source_id": url_hash})
    return True


def list_sources() -> list[dict]:
    """List all indexed sources (summary only).

    Returns:
        List of source summaries with url, shi_prefix, total_chunks.
    """
    db = get_db()
    cursor = db.sources.find(
        {},
        {"_id": 0, "url": 1, "shi_prefix": 1, "total_chunks": 1, "url_hash": 1, "indexed_at": 1},
    )
    return list(cursor)


def count_sources() -> int:
    """Count total indexed sources.

    Returns:
        Number of sources in the sources collection.
    """
    db = get_db()
    return db.sources.count_documents({})


# ---------------------------------------------------------------------------
# DATA (chunks + embeddings)
# ---------------------------------------------------------------------------

def save_data_chunks(source_id: str, chunks: list[dict]) -> bool:
    """Insert chunks with embeddings into the data collection.

    Each chunk dict should include:
        chunk_index, text, sentences, css_selector, shi_prefix, url
    Embeddings are generated automatically if the model is available.

    Args:
        source_id: Identifier linking back to the source (url_hash).
        chunks: List of chunk dicts.

    Returns:
        True if successful.
    """
    db = get_db()
    for chunk in chunks:
        chunk["source_id"] = source_id
        # Generate embedding for this chunk
        chunk_text = chunk.get("text", "")
        if chunk_text:
            embedding = embed_text(chunk_text)
            if embedding:
                chunk["embedding"] = embedding
        db.data.update_one(
            {"source_id": source_id, "chunk_index": chunk["chunk_index"]},
            {"$set": chunk},
            upsert=True,
        )
    return True


def get_chunks_by_source(source_id: str) -> list[dict]:
    """Get all data chunks for a given source.

    Args:
        source_id: The source identifier (url_hash).

    Returns:
        List of chunk dicts (without embedding field to save bandwidth).
    """
    db = get_db()
    cursor = db.data.find(
        {"source_id": source_id},
        {"_id": False, "embedding": False},
    ).sort("chunk_index", ASCENDING)
    return list(cursor)


def search_chunks(query: str, limit: int = 5) -> list[dict]:
    """Search across all data chunks by keyword.

    Uses MongoDB regex for efficient searching.
    Falls back to keyword matching when no embedding model is available.

    Args:
        query: Search query string.
        limit: Maximum number of results to return.

    Returns:
        List of matching chunks with source metadata.
    """
    db = get_db()
    query_lower = query.lower()

    pipeline = [
        {"$match": {
            "$or": [
                {"text": {"$regex": re.escape(query_lower), "$options": "i"}},
                {"sentences": {"$regex": re.escape(query_lower), "$options": "i"}},
            ]
        }},
        {"$lookup": {
            "from": "sources",
            "localField": "source_id",
            "foreignField": "url_hash",
            "as": "source_info",
        }},
        {"$unwind": {"path": "$source_info", "preserveNullAndEmptyArrays": True}},
        {"$addFields": {
            "score": {"$size": {
                "$filter": {
                    "input": "$sentences",
                    "as": "s",
                    "cond": {
                        "$regexMatch": {
                            "input": {"$toLower": "$$s"},
                            "regex": re.escape(query_lower),
                        }
                    }
                }
            }}
        }},
        {"$project": {
            "_id": 0,
            "embedding": 0,
            "source_id": 0,
            "source_info": 0,
        }},
        {"$sort": {"score": -1}},
        {"$limit": limit},
    ]

    results = list(db.data.aggregate(pipeline))

    # Add url from source_info after aggregation (MongoDB 4.4 compat)
    for r in results:
        if "url" not in r or not r["url"]:
            r["url"] = ""

    return results


def vector_search(query: str, limit: int = 5, min_score: float = 0.7) -> list[dict]:
    """Search chunks by semantic similarity using cosine similarity on embeddings.

    Computes cosine similarity in Python since MongoDB 4.4 doesn't support
    Atlas $vectorSearch. Falls back to keyword search if no embedding model.

    Args:
        query: Search query string.
        limit: Maximum number of results to return.
        min_score: Minimum cosine similarity threshold (0-1).

    Returns:
        List of matching chunks with source metadata, sorted by similarity.
    """
    # Generate query embedding
    query_embedding = embed_text(query)
    if not query_embedding:
        # Fall back to keyword search
        return search_chunks(query, limit)

    db = get_db()

    # Fetch all chunks with embeddings (limit to a reasonable number)
    cursor = db.data.find(
        {"embedding": {"$exists": True}},
        {"_id": False},
    ).limit(200)

    scored_results = []
    for chunk in cursor:
        chunk_embedding = chunk.get("embedding")
        if not chunk_embedding:
            continue
        score = cosine_similarity(query_embedding, chunk_embedding)
        if score >= min_score:
            # Look up source info
            source_info = db.sources.find_one(
                {"url_hash": chunk.get("source_id")},
                {"_id": False, "url": 1},
            )
            scored_results.append({
                "url": source_info["url"] if source_info else "",
                "shi_prefix": chunk.get("shi_prefix", ""),
                "chunk_index": chunk.get("chunk_index"),
                "css_selector": chunk.get("css_selector", ""),
                "text": chunk.get("text", ""),
                "sentences": chunk.get("sentences", []),
                "score": round(score, 4),
            })

    # Sort by score descending
    scored_results.sort(key=lambda x: x["score"], reverse=True)
    return scored_results[:limit]


def delete_chunks_by_source(source_id: str) -> bool:
    """Delete all data chunks for a given source.

    Args:
        source_id: The source identifier (url_hash).

    Returns:
        True if successful.
    """
    db = get_db()
    db.data.delete_many({"source_id": source_id})
    return True


# ---------------------------------------------------------------------------
# CLAIMS
# ---------------------------------------------------------------------------

def save_claim(claim_data: dict) -> bool:
    """Save a claim entry to the claims collection.

    Args:
        claim_data: Dict with keys: claim_id, shi_prefix, source_url, css_selector, claim_text, verified.

    Returns:
        True if successful.
    """
    db = get_db()
    claim_data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.claims.update_one(
        {"claim_id": claim_data["claim_id"]},
        {"$set": claim_data},
        upsert=True,
    )
    return True


def get_claim(claim_id: str) -> Optional[dict]:
    """Get a claim by its ID.

    Args:
        claim_id: The claim ID (e.g., "C1").

    Returns:
        Claim dict or None.
    """
    db = get_db()
    return db.claims.find_one({"claim_id": claim_id}, {"_id": False})


def list_claims() -> list[dict]:
    """List all claims.

    Returns:
        List of claim dicts.
    """
    db = get_db()
    cursor = db.claims.find({}, {"_id": False}).sort("claim_id", ASCENDING)
    return list(cursor)


def delete_claim(claim_id: str) -> bool:
    """Delete a claim by its ID.

    Args:
        claim_id: The claim ID (e.g., "C1").

    Returns:
        True if deleted, False if not found.
    """
    db = get_db()
    result = db.claims.delete_one({"claim_id": claim_id})
    return result.deleted_count > 0


# ---------------------------------------------------------------------------
# RELATIONSHIPS
# ---------------------------------------------------------------------------

def save_relationship(rel_data: dict) -> bool:
    """Save a relationship entry to the relationships collection.

    Args:
        rel_data: Dict with keys: rel_id, rel_type, claim_ids, synthesis_text.

    Returns:
        True if successful.
    """
    db = get_db()
    rel_data["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.relationships.update_one(
        {"rel_id": rel_data["rel_id"]},
        {"$set": rel_data},
        upsert=True,
    )
    return True


def get_relationship(rel_id: str) -> Optional[dict]:
    """Get a relationship by its ID.

    Args:
        rel_id: The relationship ID (e.g., "R1").

    Returns:
        Relationship dict or None.
    """
    db = get_db()
    return db.relationships.find_one({"rel_id": rel_id}, {"_id": False})


def list_relationships() -> list[dict]:
    """List all relationships.

    Returns:
        List of relationship dicts.
    """
    db = get_db()
    cursor = db.relationships.find({}, {"_id": False}).sort("rel_id", ASCENDING)
    return list(cursor)


def delete_relationship(rel_id: str) -> bool:
    """Delete a relationship by its ID.

    Args:
        rel_id: The relationship ID (e.g., "R1").

    Returns:
        True if deleted, False if not found.
    """
    db = get_db()
    result = db.relationships.delete_one({"rel_id": rel_id})
    return result.deleted_count > 0


# ---------------------------------------------------------------------------
# VAR ENTRIES
# ---------------------------------------------------------------------------

def save_var_entry(var_data: dict) -> bool:
    """Save a Veracity Audit Registry entry.

    Args:
        var_data: Dict with keys: protocol, generated_at, ssr_entries, rar_entries.

    Returns:
        True if successful.
    """
    db = get_db()
    db.var_entries.insert_one(var_data)
    return True


def list_var_entries(limit: int = 10) -> list[dict]:
    """List recent VAR entries.

    Args:
        limit: Maximum number of entries to return.

    Returns:
        List of VAR entry dicts.
    """
    db = get_db()
    cursor = db.var_entries.find(
        {},
        {"_id": False},
    ).sort("generated_at", -1).limit(limit)
    return list(cursor)


# ---------------------------------------------------------------------------
# DANGER: RESET DATABASE
# ---------------------------------------------------------------------------

def reset_database() -> dict:
    """Drop all ACG Protocol collections and recreate them.

    WARNING: This permanently deletes all indexed data.

    Returns:
        Dict with status and collections dropped.
    """
    db = get_db()
    collections = ["sources", "data", "claims", "relationships", "var_entries"]
    dropped = []
    for coll_name in collections:
        if coll_name in db.list_collection_names():
            db[coll_name].drop()
            dropped.append(coll_name)
    # Reset the db singleton to force index recreation on next access
    global _db
    _db = None
    return {"status": "reset", "collections_dropped": dropped}


def clear_collections(collections: list[str] | None = None) -> dict:
    """Clear all documents from specified collections without dropping indexes.

    Faster than reset_database() when you want to keep collection indexes.
    If no collections specified, clears all ACG collections.

    Args:
        collections: List of collection names to clear.
                     Defaults to ["sources", "data", "claims", "relationships", "var_entries"].

    Returns:
        Dict with status and counts of deleted documents per collection.
    """
    if collections is None:
        collections = ["sources", "data", "claims", "relationships", "var_entries"]

    db = get_db()
    deleted_counts = {}
    for coll_name in collections:
        if coll_name in db.list_collection_names():
            count = db[coll_name].count_documents({})
            if count > 0:
                db[coll_name].delete_many({})
            deleted_counts[coll_name] = count
        else:
            deleted_counts[coll_name] = 0

    return {"status": "cleared", "deleted_counts": deleted_counts}
