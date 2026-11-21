"""MongoDB connection and CRUD operations for the ACG Protocol.

Uses a unified collection design matching the reference implementation:
- sources: Source documents (url, shi_prefix, url_hash, full_text_length, indexed_at)
- data: Chunks with embeddings (source_id, chunk_index, text, sentences, css_selector, embedding)
- claims: Claim markers (claim_id, shi_prefix, source_url, css_selector, claim_text, verified)
- relationships: RSVP relationships (rel_id, rel_type, claim_ids, synthesis_text)
- var_entries: Veracity Audit Registry entries (protocol, generated_at, ssr_entries, rar_entries)

Searches run over an in-process cache of the corpus (``corpus.py``), which
is synced incrementally from MongoDB before each search. Pulling every
embedding over the network per query took minutes on a remote cluster.
Keyword search works without an embedding model.
"""

import os
import re
import math
import logging
import datetime
import threading
from typing import Optional

import numpy as np

from pymongo import MongoClient, ASCENDING, UpdateOne
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError


# Default MongoDB URI - override with MONGO_URI env var
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "acg_protocol")

_client = None
_db = None

# Lazy-loaded embedding model
_embedding_model = None
_embedding_failed = False

# Tools run in worker threads, so the lazy singletons need locks.
_init_lock = threading.RLock()
_model_lock = threading.Lock()

logger = logging.getLogger(__name__)


def get_client() -> MongoClient:
    """Get or create the MongoDB client singleton.

    Returns:
        MongoClient instance.

    Raises:
        ConnectionFailure: If MongoDB is unreachable.
    """
    global _client
    with _init_lock:
        return _get_client_locked()


def _get_client_locked() -> MongoClient:
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
    if _db is not None:
        return _db
    with _init_lock:
        return _get_db_locked()


def _get_db_locked():
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
        # Claim IDs are per-document (each grounded text restarts at C1), so
        # uniqueness must be scoped to (shi_prefix, claim_id) — otherwise C1
        # from source A silently overwrites C1 from source B.
        #
        # Migration: drop the legacy bare claim_id unique index (auto-named
        # "claim_id_1") if present; it conflicts with the new schema below.
        try:
            _db.claims.drop_index("claim_id_1")
        except Exception:
            pass  # index never existed
        _db.claims.create_index(
            [("shi_prefix", ASCENDING), ("claim_id", ASCENDING)], unique=True
        )
        _db.claims.create_index([("claim_id", ASCENDING)])

        # --- relationships collection ---
        _db.relationships.create_index([("rel_id", ASCENDING)], unique=True)

        # --- var_entries collection ---
        _db.var_entries.create_index([("generated_at", ASCENDING)])

    return _db


def close_connection() -> None:
    """Close the MongoDB connection."""
    global _client, _db, _embedding_model, _embedding_failed
    if _client is not None:
        _client.close()
    _client = None
    _db = None
    _embedding_model = None
    _embedding_failed = False


# ---------------------------------------------------------------------------
# EMBEDDING MODEL (lazy-loaded)
# ---------------------------------------------------------------------------

def get_embedding_model():
    """Get or create the embedding model singleton.

    Uses fastembed with BAAI/bge-small-en-v1.5 (384-dim, ~33MB).
    Returns None (and does not retry) if fastembed is unavailable.

    Returns:
        TextEmbedding instance or None.
    """
    global _embedding_model, _embedding_failed
    if _embedding_model is not None or _embedding_failed:
        return _embedding_model
    with _model_lock:
        if _embedding_model is None and not _embedding_failed:
            try:
                from fastembed import TextEmbedding
                _embedding_model = TextEmbedding(
                    "BAAI/bge-small-en-v1.5",
                    cache_dir=os.environ.get("EMBEDDING_CACHE_DIR", None),
                )
            except Exception as e:
                logger.warning(
                    f"Failed to load embedding model: {e}. "
                    "Vector search will fall back to keyword search."
                )
                _embedding_failed = True
    return _embedding_model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed several texts in one batched model call.

    Args:
        texts: Texts to embed.

    Returns:
        One embedding per text (empty lists if the model is unavailable).
    """
    if not texts:
        return []
    model = get_embedding_model()
    if model is None:
        return [[] for _ in texts]
    try:
        return [e.tolist() for e in model.embed(list(texts))]
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return [[] for _ in texts]


def embed_text(text: str) -> list[float]:
    """Generate an embedding vector for a text string.

    Args:
        text: Text to embed.

    Returns:
        List of floats (384-dim embedding), or empty list if model unavailable.
    """
    return embed_texts([text])[0]


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
    Embeddings are generated in one batched model call (if the model is
    available) and all chunks are written in one bulk request.

    Args:
        source_id: Identifier linking back to the source (url_hash).
        chunks: List of chunk dicts.

    Returns:
        True if successful.
    """
    if not chunks:
        return True
    db = get_db()
    to_embed = [i for i, c in enumerate(chunks) if c.get("text")]
    embeddings = embed_texts([chunks[i]["text"] for i in to_embed])
    for i, emb in zip(to_embed, embeddings):
        if emb:
            chunks[i]["embedding"] = emb
    ops = []
    for chunk in chunks:
        chunk["source_id"] = source_id
        ops.append(UpdateOne(
            {"source_id": source_id, "chunk_index": chunk["chunk_index"]},
            {"$set": chunk},
            upsert=True,
        ))
    db.data.bulk_write(ops, ordered=False)
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


def get_chunk_window(source_id: str, chunk_index: int, radius: int = 1) -> list[dict]:
    """Get a chunk and its neighbours (``chunk_index ± radius``) for a source.

    Used by the verifier to check a claim against the exact location its
    Claim Marker points at, while tolerating sentences that straddle a
    chunk boundary.

    Args:
        source_id: The source identifier (url_hash).
        chunk_index: Index of the target chunk.
        radius: Number of neighbouring chunks to include on each side.

    Returns:
        Chunk dicts (without embeddings) sorted by chunk_index. Empty if the
        target chunk does not exist.
    """
    db = get_db()
    cursor = db.data.find(
        {
            "source_id": source_id,
            "chunk_index": {"$gte": chunk_index - radius, "$lte": chunk_index + radius},
        },
        {"_id": False, "embedding": False},
    ).sort("chunk_index", ASCENDING)
    chunks = list(cursor)
    if not any(c.get("chunk_index") == chunk_index for c in chunks):
        return []
    return chunks


def _synced_corpus():
    """Return the in-process corpus cache, synced with the database."""
    from src.acg.corpus import get_corpus

    corpus = get_corpus(DB_NAME, MONGO_URI)
    corpus.sync(get_db())
    return corpus


# Tokens up to this length must match a whole word; longer ones may match
# as a word prefix. As substrings, short tokens ("ai", "wp", "mcp") hit
# almost every chunk ("ai" in "maintain"); prefix matching lets "agent"
# also find "agents".
_EXACT_TOKEN_MAX_LEN = 3


def _token_is_prefix(token: str) -> bool:
    return len(token) > _EXACT_TOKEN_MAX_LEN


def get_neighbour_chunks(url: str, chunk_index: int, radius: int = 2) -> list[dict]:
    """Chunks ``chunk_index ± radius`` of a source, served from the corpus cache.

    Returns an empty list if the source is not cached.
    """
    corpus = _synced_corpus()
    return [
        c for c in corpus.chunks_for_url(url)
        if c.get("chunk_index") is not None and abs(c["chunk_index"] - chunk_index) <= radius
    ]


def _token_pattern(token: str) -> re.Pattern:
    """Regex equivalent of the inverted-index match (word or word prefix)."""
    if not _token_is_prefix(token):
        return re.compile(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])")
    return re.compile(rf"(?<![a-z0-9]){re.escape(token)}")


def _chunk_result(chunk: dict, score: float) -> dict:
    return {
        "url": chunk.get("url", ""),
        "shi_prefix": chunk.get("shi_prefix", ""),
        "chunk_index": chunk.get("chunk_index"),
        "css_selector": chunk.get("css_selector", ""),
        "text": chunk.get("text", ""),
        "sentences": list(chunk.get("sentences", [])),
        "score": round(float(score), 4),
    }


def search_chunks(query: str, limit: int = 5) -> list[dict]:
    """Search across all data chunks by keyword (tokenized, IDF-weighted).

    The query is tokenized (stopwords dropped) and a chunk matches if it
    contains ANY query token at a word start. Chunks are ranked by:

    1. Weighted token coverage — the share of the query's IDF weight the
       chunk contains (primary). Rare, distinctive terms ("wordpress") count
       for more than terms found all over the corpus ("code", "ai"), so a
       chunk missing the distinctive term cannot reach full coverage.
    2. Match frequency — number of sentences containing any token (secondary)

    Args:
        query: Search query string.
        limit: Maximum number of results to return.

    Returns:
        List of matching chunks with source metadata, sorted by relevance.
        ``score`` is ``coverage * 10 + min(freq, 5) * 0.1`` and
        ``keyword_coverage`` is the 0-1 weighted coverage.
    """
    tokens = list(dict.fromkeys(_tokenize_query(query)))
    if not tokens:
        return []

    corpus = _synced_corpus()
    with corpus.lock:
        n = len(corpus.chunks)
        if n == 0:
            return []
        patterns = {t: _token_pattern(t) for t in tokens}
        hits = {t: corpus.token_hits(t, _token_is_prefix(t)) for t in tokens}
        weights = {t: corpus.idf(len(hits[t])) for t in tokens}
        total_weight = sum(weights.values())

        coverage = np.zeros(n, dtype=np.float64)
        for t in tokens:
            coverage[hits[t]] += weights[t]
        coverage /= total_weight

        candidates = np.nonzero(coverage)[0]
        if not len(candidates):
            return []
        order = candidates[np.argsort(-coverage[candidates], kind="stable")]
        # Sentence frequency is only a tie-breaker; compute it for the head.
        head = order[: max(limit * 5, 50)]

        scored = []
        for i in head:
            chunk = corpus.chunks[i]
            freq = sum(
                1 for sent in chunk["sentences"]
                if any(p.search(sent.lower()) for p in patterns.values())
            )
            r = _chunk_result(chunk, coverage[i] * 10.0 + min(freq, 5) * 0.1)
            r["keyword_coverage"] = round(float(coverage[i]), 4)
            scored.append(r)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


# Common English stopwords — excluded from keyword search tokens
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "does",
    "did", "for", "from", "had", "has", "have", "how", "in", "is", "it",
    "its", "of", "on", "or", "that", "the", "this", "these", "those",
    "to", "was", "were", "what", "when", "where", "which", "who", "why",
    "with", "not", "no", "we", "you", "he", "she", "they",
})


def _tokenize_query(query: str) -> list[str]:
    """Split a search query into meaningful lowercase tokens.

    Strips punctuation, lowercases, drops stopwords and 1-char tokens.
    Preserves code/identifier terms (e.g. "bge-small-en-v1.5", "PLAYWRIGHT").

    Args:
        query: Raw search query.

    Returns:
        List of normalized search tokens.
    """
    raw = re.findall(r"[a-zA-Z0-9]+", query.lower())
    return [t for t in raw if len(t) >= 2 and t not in _STOPWORDS]


def vector_search(
    query: str, limit: int = 5, min_score: float = 0.7, fallback: bool = True
) -> list[dict]:
    """Search chunks by semantic similarity (cosine) over the corpus cache.

    Every embedded chunk is scored with one matrix product in memory; no
    per-query transfer of embeddings from MongoDB.

    Args:
        query: Search query string.
        limit: Maximum number of results to return.
        min_score: Minimum cosine similarity threshold (0-1).
        fallback: If True and no embedding model is available, return
            keyword search results instead. ``hybrid_search`` passes False
            so keyword scores are never mistaken for cosine scores.

    Returns:
        List of matching chunks with source metadata, sorted by similarity.
    """
    query_embedding = embed_text(query)
    if not query_embedding:
        return search_chunks(query, limit) if fallback else []

    corpus = _synced_corpus()
    with corpus.lock:
        scores = corpus.vector_scores(query_embedding)
        idx = np.nonzero(scores >= min_score)[0]
        idx = idx[np.argsort(-scores[idx], kind="stable")][:limit]
        return [_chunk_result(corpus.chunks[i], scores[i]) for i in idx]


# ---------------------------------------------------------------------------
# HYBRID SEARCH (vector + keyword, fused into one ranking)
# ---------------------------------------------------------------------------

# Weights of the two signals in the fused ranking score.
HYBRID_VECTOR_WEIGHT = 0.5
HYBRID_KEYWORD_WEIGHT = 0.5
# A vector-only hit (no keyword match) must clear this cosine score to be
# returned at all, matching the old vector_search gate.
HYBRID_VECTOR_ONLY_MIN = 0.7
# Chunks that also match keywords get vector credit from this cosine score up.
HYBRID_VECTOR_FLOOR = 0.5
# search_chunks scores are coverage * 10 + up to 0.5 frequency bonus.
_KEYWORD_SCORE_SCALE = 10.0


def _vector_norm(cos: float) -> float:
    """Map cosine similarity [FLOOR, 1] onto [0, 1]."""
    span = 1.0 - HYBRID_VECTOR_FLOOR
    return max(0.0, min(1.0, (cos - HYBRID_VECTOR_FLOOR) / span))


def hybrid_search(query: str, limit: int = 5) -> list[dict]:
    """Search chunks with vector and keyword search combined into one ranking.

    Previously vector results were returned whenever any chunk cleared the
    cosine gate, so a weak semantic match on an unrelated chunk hid an exact
    keyword hit. Here both searches always run and each candidate chunk is
    ranked by::

        hybrid_score = 0.5 * vector_norm + 0.5 * keyword_norm

    where ``vector_norm`` rescales cosine [0.5, 1] to [0, 1] and
    ``keyword_norm`` is query-token coverage (0-1). A chunk matched by both
    searches therefore outranks one matched by only one of them.

    Each result keeps ``score`` on a 0-1 scale for the confidence
    calculations (the stronger of cosine similarity and keyword coverage),
    and also reports ``hybrid_score``, ``vector_score`` and ``keyword_score``.

    Args:
        query: Search query string.
        limit: Maximum number of results to return.

    Returns:
        Chunks sorted by ``hybrid_score`` (descending).
    """
    pool = max(limit * 4, 20)
    vector_hits = vector_search(query, limit=pool, min_score=HYBRID_VECTOR_FLOOR, fallback=False)
    keyword_hits = search_chunks(query, limit=pool)

    merged: dict[str, dict] = {}

    def _key(r: dict) -> str:
        return r.get("css_selector") or f"{r.get('url', '')}#{r.get('chunk_index')}"

    for r in keyword_hits:
        kw_raw = float(r.get("score", 0.0))
        entry = dict(r)
        entry["keyword_score"] = kw_raw
        entry["vector_score"] = None
        entry["_kw_norm"] = min(kw_raw / _KEYWORD_SCORE_SCALE, 1.0)
        merged[_key(r)] = entry

    for r in vector_hits:
        cos = float(r.get("score", 0.0))
        k = _key(r)
        if k in merged:
            merged[k]["vector_score"] = cos
            for field in ("url", "shi_prefix", "text", "sentences"):
                merged[k].setdefault(field, r.get(field))
        elif cos >= HYBRID_VECTOR_ONLY_MIN:
            entry = dict(r)
            entry["vector_score"] = cos
            entry["keyword_score"] = None
            entry["_kw_norm"] = 0.0
            merged[k] = entry

    results = []
    for entry in merged.values():
        cos = entry["vector_score"] or 0.0
        kw_norm = entry.pop("_kw_norm")
        entry["hybrid_score"] = round(
            HYBRID_VECTOR_WEIGHT * _vector_norm(cos) + HYBRID_KEYWORD_WEIGHT * kw_norm, 4
        )
        entry["score"] = round(max(cos, kw_norm), 4)
        results.append(entry)

    results.sort(key=lambda x: (x["hybrid_score"], x["score"]), reverse=True)
    return results[:limit]


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
    # Upsert scoped to (shi_prefix, claim_id): claim IDs repeat across
    # grounded texts, so the prefix disambiguates which source a claim
    # belongs to (prevents cross-source overwrite).
    db.claims.update_one(
        {"shi_prefix": claim_data.get("shi_prefix", ""), "claim_id": claim_data["claim_id"]},
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
    from src.acg.corpus import reset_corpus
    reset_corpus()
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

    from src.acg.corpus import reset_corpus
    reset_corpus()
    return {"status": "cleared", "deleted_counts": deleted_counts}
