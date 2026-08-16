"""Tests for the three verified db.py bug fixes ported from webforge:

1. ``search_chunks`` recall bug — a contiguous-phrase ``$regex`` failed to
   match multi-word queries that are not verbatim in the text (e.g.
   "chat for streaming" vs "How to stream a chat completion"), producing
   false-LOW confidence and forcing unnecessary web_fetch calls. Fixed with
   a tokenized OR-regex plus coverage scoring.

2. ``vector_search`` hard-coded 200-chunk cap — silently skipped most of the
   index, also causing false LOW confidence. Fixed by making the candidate
   cap configurable via ``ACG_VECTOR_MAX_CANDIDATES`` (default 10000).

3. ``claims`` claim_id uniqueness — claim IDs restart at C1 for every
   grounded text, so a bare unique index on ``claim_id`` caused C1 from one
   source to silently overwrite C1 from another. Fixed with a compound
   unique index on ``(shi_prefix, claim_id)`` + legacy index migration, and
   an upsert filter scoped to the same compound key.

All MongoDB access is mocked — no live instance required.
"""

import os
import re
import sys

sys.path.insert(0, ".")

import src.acg.db as db


# ---------------------------------------------------------------------------
# Fake MongoDB primitives
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self.limit_value = None

    def limit(self, n):
        self.limit_value = n
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.create_index_calls = []
        self.drop_index_calls = []

    def create_index(self, keys, **kwargs):
        self.create_index_calls.append((keys, kwargs))
        return f"{self.name}_idx_{len(self.create_index_calls)}"

    def drop_index(self, name):
        self.drop_index_calls.append(name)


class FakeDataCollection(FakeCollection):
    """Emulates Mongo $match/$or regex filtering for the data collection."""

    def __init__(self, docs=None):
        super().__init__("data")
        self.docs = list(docs or [])
        self.last_pipeline = None
        self.last_find_filter = None
        self.last_cursor_limit = None

    def aggregate(self, pipeline):
        self.last_pipeline = pipeline
        results = []
        for doc in self.docs:
            if self._matches(doc, pipeline):
                results.append(dict(doc))
        return results

    def find(self, filt, projection=None):
        self.last_find_filter = filt
        cursor = FakeCursor(self.docs)
        original_limit = cursor.limit

        def tracked_limit(n):
            self.last_cursor_limit = n
            return original_limit(n)

        cursor.limit = tracked_limit
        return cursor

    @staticmethod
    def _matches(doc, pipeline):
        for stage in pipeline:
            if "$match" not in stage:
                continue
            cond = stage["$match"]
            if "$or" not in cond:
                continue
            text = doc.get("text") or ""
            sentences = " ".join(doc.get("sentences") or [])
            for entry in cond["$or"]:
                for field, spec in entry.items():
                    if field not in ("text", "sentences"):
                        continue
                    haystack = text if field == "text" else sentences
                    regex = spec.get("$regex", "")
                    options = spec.get("$options", "")
                    flags = re.IGNORECASE if "i" in options else 0
                    if re.search(regex, haystack, flags):
                        return True
        return False


class FakeSourcesCollection(FakeCollection):
    def find_one(self, filt, projection=None):
        url_hash = filt.get("url_hash", "unknown")
        return {"url": f"https://example.com/{url_hash}"}


class FakeDatabase:
    """Mimics a pymongo Database: attribute/item access to collections."""

    def __init__(self, cols):
        self._cols = cols

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection(name)
        return self._cols[name]

    def __getattr__(self, name):
        return self[name]


class FakeClient:
    def __init__(self, collections=None):
        self._cols = collections or {}
        self._cols.setdefault("data", FakeDataCollection())

    def __getitem__(self, name):
        # client[DB_NAME] -> a database object (collections via attrs)
        return FakeDatabase(self._cols)


# ---------------------------------------------------------------------------
# Fix 1: search_chunks recall (tokenized OR-regex + coverage scoring)
# ---------------------------------------------------------------------------

class TestSearchChunksRecall:
    def test_tokenize_query_drops_stopwords(self):
        assert db._tokenize_query("How to stream a chat completion") == [
            "stream", "chat", "completion",
        ]

    def test_tokenize_query_empty_stopwords_only(self):
        assert db._tokenize_query("the and of") == []

    def test_search_chunks_empty_tokens_returns_empty(self, monkeypatch):
        fake_db = FakeDatabase({})
        monkeypatch.setattr(db, "get_db", lambda: fake_db)
        assert db.search_chunks("the and of", limit=5) == []

    def test_search_chunks_matches_non_verbatim_query(self, monkeypatch):
        """'chat for streaming' must match 'How to stream a chat completion...'."""
        docs = [
            {
                "chunk_index": 0,
                "text": "How to stream a chat completion with the API.",
                "sentences": ["How to stream a chat completion with the API."],
                "shi_prefix": "a1",
                "url": "https://example.com/1",
            },
            {
                "chunk_index": 1,
                "text": "The documentation covers installation only.",
                "sentences": ["The documentation covers installation only."],
                "shi_prefix": "b2",
                "url": "https://example.com/2",
            },
        ]
        data = FakeDataCollection(docs)
        fake_db = FakeDatabase({"data": data})
        monkeypatch.setattr(db, "get_db", lambda: fake_db)

        results = db.search_chunks("chat for streaming", limit=5)

        urls = [r["url"] for r in results]
        assert "https://example.com/1" in urls, (
            "Non-verbatim multi-word query must still find the relevant chunk"
        )
        # The $match regex must be a tokenized OR-regex, not the raw phrase
        match_stage = data.last_pipeline[0]["$match"]
        or_regex = match_stage["$or"][0]["text"]["$regex"]
        assert "chat" in or_regex and "streaming" in or_regex
        assert "|" in or_regex
        assert "for" not in or_regex  # stopword must be excluded

    def test_search_chunks_ranks_full_token_coverage_first(self, monkeypatch):
        """A chunk matching ALL query tokens ranks above a partial match."""
        docs = [
            {
                "chunk_index": 0,
                "text": "chat streaming is covered later in the guide",
                "sentences": ["chat streaming is covered later in the guide"],
                "shi_prefix": "partial",
                "url": "https://example.com/partial",
            },
            {
                "chunk_index": 1,
                "text": "chat streaming completion tokens explained",
                "sentences": ["chat streaming completion tokens explained"],
                "shi_prefix": "full",
                "url": "https://example.com/full",
            },
        ]
        data = FakeDataCollection(docs)
        fake_db = FakeDatabase({"data": data})
        monkeypatch.setattr(db, "get_db", lambda: fake_db)

        results = db.search_chunks("chat streaming completion", limit=5)

        assert results, "Expected at least one result"
        assert results[0]["url"] == "https://example.com/full", (
            "Chunk matching all tokens must rank above partial match"
        )

    def test_search_chunks_handles_no_candidates(self, monkeypatch):
        data = FakeDataCollection([])
        fake_db = FakeDatabase({"data": data})
        monkeypatch.setattr(db, "get_db", lambda: fake_db)
        assert db.search_chunks("nonexistenttermxyz", limit=5) == []


# ---------------------------------------------------------------------------
# Fix 2: vector_search configurable candidate cap
# ---------------------------------------------------------------------------

class TestVectorSearchCap:
    def _embedded_docs(self, n, hot_index=None):
        """n chunks; the chunk at hot_index (if any) is similar to [1,0]."""
        docs = []
        for i in range(n):
            embedding = [1.0, 0.0] if i == hot_index else [0.0, 1.0]
            docs.append({
                "source_id": f"s{i}",
                "embedding": embedding,
                "shi_prefix": f"p{i}",
                "chunk_index": i,
                "css_selector": f"#acg-chunk-{i}",
                "text": f"chunk {i}",
                "sentences": [f"chunk {i}"],
            })
        return docs

    def test_vector_search_default_cap_is_not_200(self, monkeypatch):
        """A match beyond the old 200-chunk cap must still be found."""
        os.environ.pop("ACG_VECTOR_MAX_CANDIDATES", None)
        monkeypatch.setattr(db, "embed_text", lambda t: [1.0, 0.0])

        docs = self._embedded_docs(300, hot_index=250)
        data = FakeDataCollection(docs)
        fake_db = FakeDatabase({"data": data, "sources": FakeSourcesCollection("sources")})
        monkeypatch.setattr(db, "get_db", lambda: fake_db)

        results = db.vector_search("query", limit=5, min_score=0.9)

        assert data.last_cursor_limit is not None
        assert data.last_cursor_limit != 200, (
            "Candidate cap must not be the old hard-coded 200"
        )
        urls = [r["url"] for r in results]
        assert "https://example.com/s250" in urls, (
            "Chunk beyond old 200-cap must be reachable (no silent truncation)"
        )

    def test_vector_search_cap_configurable_via_env(self, monkeypatch):
        os.environ["ACG_VECTOR_MAX_CANDIDATES"] = "7"
        try:
            monkeypatch.setattr(db, "embed_text", lambda t: [1.0, 0.0])
            docs = self._embedded_docs(10, hot_index=3)
            data = FakeDataCollection(docs)
            fake_db = FakeDatabase({"data": data, "sources": FakeSourcesCollection("sources")})
            monkeypatch.setattr(db, "get_db", lambda: fake_db)

            db.vector_search("query", limit=5, min_score=0.9)

            assert data.last_cursor_limit == 7, (
                "ACG_VECTOR_MAX_CANDIDATES must control the candidate cap"
            )
        finally:
            os.environ.pop("ACG_VECTOR_MAX_CANDIDATES", None)


# ---------------------------------------------------------------------------
# Fix 3: claims compound (shi_prefix, claim_id) uniqueness
# ---------------------------------------------------------------------------

class TestClaimsCompoundIndex:
    def test_claims_compound_unique_index_created(self, monkeypatch):
        fake_client = FakeClient({})
        monkeypatch.setattr(db, "get_client", lambda: fake_client)
        db._db = None

        d = db.get_db()
        claims = d["claims"]
        index_keys = [keys for keys, _ in claims.create_index_calls]

        # Compound (shi_prefix, claim_id) index must exist and be unique
        compound = [keys for keys in index_keys if list(dict(keys).keys()) == ["shi_prefix", "claim_id"]]
        assert compound, "Missing compound (shi_prefix, claim_id) index"
        idx_kwargs = [kw for keys, kw in claims.create_index_calls if keys == compound[0]]
        assert idx_kwargs[0].get("unique") is True

        # Bare claim_id index must NOT be unique anymore
        bare = [keys for keys in index_keys if list(dict(keys).keys()) == ["claim_id"]]
        assert bare, "Expected a non-unique claim_id index to remain"
        bare_kwargs = [kw for keys, kw in claims.create_index_calls if keys == bare[0]]
        assert bare_kwargs[0].get("unique") is not True

        # Legacy bare unique index must be dropped (migration)
        assert "claim_id_1" in claims.drop_index_calls

    def test_save_claim_upsert_scoped_to_shi_prefix(self, monkeypatch):
        captured = {}

        class FakeClaims(FakeCollection):
            def update_one(self, filt, update, upsert=False):
                captured["filter"] = filt
                return None

        fake_db = FakeDatabase({"claims": FakeClaims("claims")})
        monkeypatch.setattr(db, "get_db", lambda: fake_db)

        db.save_claim({
            "claim_id": "C1",
            "shi_prefix": "abc123def456",
            "source_url": "https://example.com",
            "css_selector": "#p1",
            "claim_text": "A claim",
            "verified": False,
        })

        assert captured["filter"] == {"shi_prefix": "abc123def456", "claim_id": "C1"}, (
            "Claim upsert must be scoped to (shi_prefix, claim_id) so C1 from "
            "different sources never overwrite each other"
        )

    def test_same_claim_id_different_sources_coexist(self, monkeypatch):
        """C1 from source A and C1 from source B must both persist."""
        stored = []

        class FakeClaims(FakeCollection):
            def update_one(self, filt, update, upsert=False):
                doc = dict(update["$set"])
                doc["_key"] = filt
                stored.append(doc)
                return None

        fake_db = FakeDatabase({"claims": FakeClaims("claims")})
        monkeypatch.setattr(db, "get_db", lambda: fake_db)

        for shi in ("aaa111", "bbb222"):
            db.save_claim({
                "claim_id": "C1",
                "shi_prefix": shi,
                "source_url": f"https://example.com/{shi}",
                "css_selector": "#p1",
                "claim_text": "claim",
                "verified": False,
            })

        keys = [doc["_key"]["shi_prefix"] for doc in stored]
        assert keys == ["aaa111", "bbb222"], (
            "Identical claim_id from different shi_prefixes must not collide"
        )
