"""In-process cache of the indexed corpus (chunk text + embeddings).

Why this exists: vector search used to pull every chunk *and* its
384-float embedding from MongoDB on every query, then issue one extra
``find_one`` per candidate to look up the source URL. Against a remote
cluster (e.g. Atlas, ~50 ms round trip) with ~10k chunks that is ~100 MB
and thousands of round trips per query, which took minutes.

The cache keeps every chunk's text and a normalised float32 embedding
matrix in memory. Before each search it reads the small ``sources``
collection (one cheap query) and reloads only the sources that were added,
re-indexed or removed since the last sync, so writes from other processes
sharing the database are picked up too. The cache is also saved to disk,
so a restarted server does not have to download the whole corpus again.

Search over the cache (cosine + keyword) is local and takes milliseconds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import bisect
import os
import re
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_CACHE_FORMAT = 1
_WORD_RE = re.compile(r"[a-z0-9]+")

# Source fields read on every sync. ``indexed_at`` changes on every
# (re-)index; ``shi_prefix`` and ``total_chunks`` cover legacy sources
# written without a timestamp.
_SOURCE_FIELDS = {"_id": 0, "url_hash": 1, "url": 1, "indexed_at": 1, "shi_prefix": 1, "total_chunks": 1}
_CHUNK_FIELDS = {
    "_id": 0, "source_id": 1, "chunk_index": 1, "css_selector": 1,
    "shi_prefix": 1, "sentences": 1, "text": 1, "embedding": 1,
}


def _source_version(src: dict) -> str:
    return f"{src.get('indexed_at', '')}|{src.get('shi_prefix', '')}|{src.get('total_chunks', '')}"


def _default_cache_path(db_name: str, mongo_uri: str) -> Optional[Path]:
    base = os.environ.get("ACG_CACHE_DIR")
    if base == "":
        return None  # explicitly disabled
    root = Path(base) if base else Path.home() / ".cache" / "acg-mcp"
    # One cache file per (cluster, database). Credentials are stripped before
    # hashing so rotating a password does not invalidate the cache.
    host = re.sub(r"//[^@/]*@", "//", mongo_uri)
    key = hashlib.sha256(f"{host}|{db_name}".encode()).hexdigest()[:16]
    return root / f"corpus-{key}"


class Corpus:
    """Thread-safe in-memory mirror of the ``data`` collection."""

    def __init__(self, cache_path: Optional[Path] = None):
        self._lock = threading.RLock()
        self._cache_path = cache_path
        # url_hash -> {"version", "url", "complete", "chunks": [chunk dicts]}
        self._sources: dict[str, dict] = {}
        self._dirty = True
        # Flat views rebuilt after each change.
        self.chunks: list[dict] = []
        self.matrix = np.zeros((0, 0), dtype=np.float32)
        self.lower_texts: list[str] = []
        self._token_hits: dict[str, np.ndarray] = {}
        # Inverted index: word -> chunk indices, plus the sorted vocabulary
        # for prefix lookups. Words are [a-z0-9]+ runs, the same rule the
        # query tokenizer uses.
        self._postings: dict[str, np.ndarray] = {}
        self._vocab: list[str] = []
        self._loaded_from_disk = False
        self._signature = None

    @property
    def lock(self) -> threading.RLock:
        """Hold while reading ``chunks``/``matrix`` so a sync can't swap them mid-search."""
        return self._lock

    # ------------------------------------------------------------------ sync

    @staticmethod
    def _db_signature(db):
        """Cheap change detector: source count, newest indexed_at, chunk count.

        One small aggregate plus a metadata count, instead of reading every
        source document on every search. Any index, re-index or delete
        changes at least one of these.
        """
        try:
            agg = list(db.sources.aggregate([
                {"$group": {"_id": None, "n": {"$sum": 1}, "latest": {"$max": "$indexed_at"}}}
            ]))
            n_chunks = db.data.estimated_document_count()
        except Exception:
            return None  # unknown -> always do the full diff
        head = agg[0] if agg else {}
        return (head.get("n", 0), head.get("latest"), n_chunks)

    def sync(self, db) -> None:
        """Bring the cache in line with the database (incremental)."""
        with self._lock:
            if not self._loaded_from_disk:
                self._loaded_from_disk = True
                self._load_from_disk()

            signature = self._db_signature(db)
            if signature is not None and signature == self._signature and not self._dirty:
                return

            current = {s["url_hash"]: s for s in db.sources.find({}, _SOURCE_FIELDS) if s.get("url_hash")}

            removed = [h for h in self._sources if h not in current]
            stale = [
                h for h, s in current.items()
                if h not in self._sources
                or not self._sources[h]["complete"]
                or self._sources[h]["version"] != _source_version(s)
            ]
            for h in removed:
                del self._sources[h]

            if stale:
                fetched: dict[str, list[dict]] = {h: [] for h in stale}
                # Batch the $in so a cold start does not send one giant query.
                for i in range(0, len(stale), 200):
                    batch = stale[i:i + 200]
                    for chunk in db.data.find({"source_id": {"$in": batch}}, _CHUNK_FIELDS):
                        fetched.setdefault(chunk.get("source_id"), []).append(chunk)
                for h in stale:
                    src = current[h]
                    chunks = sorted(fetched.get(h, []), key=lambda c: c.get("chunk_index", 0))
                    expected = src.get("total_chunks")
                    self._sources[h] = {
                        "version": _source_version(src),
                        "url": src.get("url", ""),
                        "shi_prefix": src.get("shi_prefix", ""),
                        # A source being (re-)indexed right now may have fewer
                        # chunks than total_chunks; reload it on the next sync.
                        "complete": expected is None or len(chunks) >= expected,
                        "chunks": [self._compact(c) for c in chunks],
                    }
                logger.info(f"Corpus cache: loaded {len(stale)} source(s), removed {len(removed)}")

            if stale or removed or self._dirty:
                self._rebuild()
                self._save_to_disk()
            self._signature = signature

    def invalidate(self, url_hash: str) -> None:
        """Force a source to be reloaded on the next sync."""
        with self._lock:
            if url_hash in self._sources:
                self._sources[url_hash]["complete"] = False
            self._signature = None

    @staticmethod
    def _compact(chunk: dict) -> dict:
        sentences = chunk.get("sentences") or []
        text = chunk.get("text") or " ".join(sentences)
        emb = chunk.get("embedding")
        return {
            "chunk_index": chunk.get("chunk_index"),
            "css_selector": chunk.get("css_selector", ""),
            "shi_prefix": chunk.get("shi_prefix", ""),
            "sentences": sentences,
            "text": text,
            "embedding": np.asarray(emb, dtype=np.float32) if emb is not None and len(emb) else None,
        }

    def _rebuild(self) -> None:
        chunks: list[dict] = []
        vectors: list = []
        dim = 0
        for h in sorted(self._sources):
            src = self._sources[h]
            for c in src["chunks"]:
                emb = c.get("embedding")
                if emb is not None and len(emb):
                    dim = dim or len(emb)
                chunks.append({
                    "url": src["url"],
                    "source_id": h,
                    "shi_prefix": c.get("shi_prefix") or src.get("shi_prefix", ""),
                    "chunk_index": c.get("chunk_index"),
                    "css_selector": c.get("css_selector", ""),
                    "text": c.get("text", ""),
                    "sentences": c.get("sentences", []),
                })
                vectors.append(emb)

        matrix = np.zeros((len(chunks), dim), dtype=np.float32)
        for i, emb in enumerate(vectors):
            if emb is not None and len(emb) == dim:
                matrix[i] = np.asarray(emb, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True) if dim else np.zeros((len(chunks), 1))
        norms[norms == 0] = 1.0
        if dim:
            matrix /= norms

        by_url: dict[str, list[dict]] = {}
        for c in chunks:
            by_url.setdefault(c["url"], []).append(c)
        self._by_url = by_url
        self.chunks = chunks
        self.matrix = matrix
        self.lower_texts = [" ".join(c["sentences"]).lower() if c["sentences"] else c["text"].lower() for c in chunks]
        postings: dict[str, list[int]] = {}
        for i, text in enumerate(self.lower_texts):
            for word in set(_WORD_RE.findall(text)):
                postings.setdefault(word, []).append(i)
        self._postings = {w: np.asarray(ids, dtype=np.int64) for w, ids in postings.items()}
        self._vocab = sorted(self._postings)
        self._token_hits = {}
        self._dirty = False

    # ------------------------------------------------------------- persistence

    def _save_to_disk(self) -> None:
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            meta = {"format": _CACHE_FORMAT, "sources": {}}
            vecs = []
            for h, src in self._sources.items():
                if not src["complete"]:
                    continue
                entry = {k: src[k] for k in ("version", "url", "shi_prefix")}
                entry["chunks"] = []
                for c in src["chunks"]:
                    emb = c.get("embedding")
                    row = -1
                    if emb is not None and len(emb):
                        row = len(vecs)
                        vecs.append(np.asarray(emb, dtype=np.float32))
                    entry["chunks"].append({
                        "chunk_index": c["chunk_index"], "css_selector": c["css_selector"],
                        "shi_prefix": c["shi_prefix"], "sentences": c["sentences"],
                        "text": c["text"], "row": row,
                    })
                meta["sources"][h] = entry
            dims = {v.shape[0] for v in vecs}
            if len(dims) > 1:
                return  # mixed embedding sizes: don't persist, rebuild from DB
            arr = np.stack(vecs) if vecs else np.zeros((0, 0), dtype=np.float32)
            tmp_npy = self._cache_path.with_suffix(".npy.tmp")
            tmp_json = self._cache_path.with_suffix(".json.tmp")
            with open(tmp_npy, "wb") as f:
                np.save(f, arr, allow_pickle=False)
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            os.replace(tmp_npy, self._cache_path.with_suffix(".npy"))
            os.replace(tmp_json, self._cache_path.with_suffix(".json"))
        except Exception as e:  # cache is an optimisation only
            logger.warning(f"Could not save corpus cache: {e}")

    def _load_from_disk(self) -> None:
        if self._cache_path is None:
            return
        json_path = self._cache_path.with_suffix(".json")
        npy_path = self._cache_path.with_suffix(".npy")
        if not (json_path.exists() and npy_path.exists()):
            return
        try:
            with open(json_path, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("format") != _CACHE_FORMAT:
                return
            arr = np.load(npy_path, allow_pickle=False)
            sources = {}
            for h, entry in meta["sources"].items():
                chunks = []
                for c in entry["chunks"]:
                    row = c.pop("row", -1)
                    c["embedding"] = arr[row] if row >= 0 else None
                    chunks.append(c)
                sources[h] = {
                    "version": entry["version"], "url": entry["url"],
                    "shi_prefix": entry.get("shi_prefix", ""), "complete": True, "chunks": chunks,
                }
            self._sources = sources
            self._dirty = True
            logger.info(f"Corpus cache: {len(sources)} source(s) loaded from {json_path}")
        except Exception as e:
            logger.warning(f"Ignoring unreadable corpus cache {json_path}: {e}")
            self._sources = {}

    # ----------------------------------------------------------------- search

    def vector_scores(self, query_embedding) -> np.ndarray:
        """Cosine similarity of the query against every cached chunk."""
        if not len(self.chunks) or self.matrix.shape[1] == 0:
            return np.zeros(len(self.chunks), dtype=np.float32)
        q = np.asarray(query_embedding, dtype=np.float32)
        if q.shape[0] != self.matrix.shape[1]:
            return np.zeros(len(self.chunks), dtype=np.float32)
        n = np.linalg.norm(q)
        if n == 0:
            return np.zeros(len(self.chunks), dtype=np.float32)
        return self.matrix @ (q / n)

    def token_hits(self, token: str, prefix: bool) -> np.ndarray:
        """Indices of chunks containing ``token`` as a word (or word prefix).

        Uses the inverted index, so a lookup costs a dictionary/bisect
        search rather than a scan over every chunk.
        """
        key = f"{token}*" if prefix else token
        with self._lock:
            hits = self._token_hits.get(key)
            if hits is None:
                if not prefix:
                    hits = self._postings.get(token, np.zeros(0, dtype=np.int64))
                else:
                    lo = bisect.bisect_left(self._vocab, token)
                    hi = bisect.bisect_left(self._vocab, token + "\uffff")
                    lists = [self._postings[w] for w in self._vocab[lo:hi]]
                    hits = np.unique(np.concatenate(lists)) if lists else np.zeros(0, dtype=np.int64)
                self._token_hits[key] = hits
            return hits

    def chunks_for_url(self, url: str) -> list[dict]:
        """Cached chunks of one source, ordered by chunk_index."""
        with self._lock:
            return list(getattr(self, "_by_url", {}).get(url, []))

    def idf(self, df: int) -> float:
        """Smoothed inverse document frequency for a token found in ``df`` chunks."""
        n = len(self.chunks)
        return math.log((n + 1) / (df + 1)) + 1.0


_corpus: Optional[Corpus] = None
_corpus_lock = threading.Lock()


def get_corpus(db_name: str, mongo_uri: str) -> Corpus:
    global _corpus
    with _corpus_lock:
        if _corpus is None:
            _corpus = Corpus(_default_cache_path(db_name, mongo_uri))
        return _corpus


def reset_corpus() -> None:
    """Drop the in-memory cache (used after a database reset and in tests)."""
    global _corpus
    with _corpus_lock:
        if _corpus is not None and _corpus._cache_path is not None:
            for suffix in (".json", ".npy"):
                try:
                    _corpus._cache_path.with_suffix(suffix).unlink()
                except FileNotFoundError:
                    pass
        _corpus = None
