"""Tests for hybrid search (issue #6 part 3).

Before: search_sources returned vector results whenever any chunk cleared
cosine 0.7, so a weak semantic match on an unrelated chunk (0.78) hid an
exact keyword hit on the right chunk. Now both searches always run and are
fused into one ranking.

vector_search / search_chunks are mocked — no MongoDB or embedding model.
"""

import sys

sys.path.insert(0, ".")

import src.acg.db as db
from src.acg.indexer import search_sources


def _chunk(sel, score, url="https://example.com", text="t"):
    return {"url": url, "shi_prefix": "aaaaaaaaaaaa", "chunk_index": int(sel.rsplit("-", 1)[1]),
            "css_selector": sel, "text": text, "sentences": [text], "score": score}


ARXIV = "#acg-chunk-arxiv000-5"
QWEN = "#acg-chunk-cc640439-13"
CODER = "#acg-chunk-c0de0000-4"


def _mock(monkeypatch, vector, keyword):
    calls = {}

    def fake_vector(query, limit=5, min_score=0.7, fallback=True):
        calls["vector"] = {"limit": limit, "min_score": min_score, "fallback": fallback}
        return [r for r in vector if r["score"] >= min_score][:limit]

    def fake_keyword(query, limit=5):
        calls["keyword"] = {"limit": limit}
        return keyword[:limit]

    monkeypatch.setattr(db, "vector_search", fake_vector)
    monkeypatch.setattr(db, "search_chunks", fake_keyword)
    return calls


class TestHybridSearch:
    def test_issue_repro_keyword_hit_beats_weak_vector_match(self, monkeypatch):
        # arXiv chunk: weak semantic match, no keyword hit.
        # Qwen chunk: exact keyword hit (score 10.3), modest vector score.
        _mock(
            monkeypatch,
            vector=[_chunk(ARXIV, 0.78), _chunk(QWEN, 0.66)],
            keyword=[_chunk(QWEN, 10.3)],
        )
        results = search_sources("Number of Layers Number of Attention Heads GQA", limit=5)
        assert results[0]["css_selector"] == QWEN
        assert results[0]["keyword_score"] == 10.3
        assert results[0]["vector_score"] == 0.66

    def test_keyword_hit_returned_even_without_vector_score(self, monkeypatch):
        _mock(monkeypatch, vector=[_chunk(ARXIV, 0.78)], keyword=[_chunk(QWEN, 10.3)])
        results = search_sources("q", limit=5)
        sels = [r["css_selector"] for r in results]
        assert sels[0] == QWEN and ARXIV in sels

    def test_chunk_matched_by_both_ranks_first(self, monkeypatch):
        _mock(
            monkeypatch,
            vector=[_chunk(ARXIV, 0.9), _chunk(QWEN, 0.8)],
            keyword=[_chunk(CODER, 10.0), _chunk(QWEN, 10.0)],
        )
        results = search_sources("q", limit=5)
        assert results[0]["css_selector"] == QWEN

    def test_weak_vector_only_hits_dropped(self, monkeypatch):
        # 0.6 clears the hybrid floor but a vector-only hit needs 0.7.
        _mock(monkeypatch, vector=[_chunk(ARXIV, 0.6)], keyword=[])
        assert search_sources("q") == []

    def test_weak_vector_score_still_boosts_keyword_hit(self, monkeypatch):
        _mock(
            monkeypatch,
            vector=[_chunk(QWEN, 0.6)],
            keyword=[_chunk(CODER, 10.0), _chunk(QWEN, 10.0)],
        )
        results = search_sources("q")
        assert results[0]["css_selector"] == QWEN
        assert results[0]["hybrid_score"] > results[1]["hybrid_score"]

    def test_score_stays_on_zero_one_scale(self, monkeypatch):
        _mock(monkeypatch, vector=[_chunk(ARXIV, 0.82)], keyword=[_chunk(QWEN, 10.5)])
        for r in search_sources("q"):
            assert 0.0 <= r["score"] <= 1.0
            assert 0.0 <= r["hybrid_score"] <= 1.0

    def test_keyword_only_when_no_embedding_model(self, monkeypatch):
        calls = _mock(monkeypatch, vector=[], keyword=[_chunk(QWEN, 10.0)])
        results = search_sources("q")
        assert [r["css_selector"] for r in results] == [QWEN]
        assert results[0]["vector_score"] is None
        assert calls["vector"]["fallback"] is False

    def test_limit_respected_and_pool_larger(self, monkeypatch):
        keyword = [_chunk(f"#acg-chunk-aaaaaaaa-{i}", 10.0 - i * 0.1) for i in range(30)]
        calls = _mock(monkeypatch, vector=[], keyword=keyword)
        results = search_sources("q", limit=3)
        assert len(results) == 3
        assert calls["keyword"]["limit"] >= 12
        assert calls["vector"]["limit"] >= 12

    def test_no_results(self, monkeypatch):
        _mock(monkeypatch, vector=[], keyword=[])
        assert search_sources("q") == []


class TestVectorSearchFallbackFlag:
    def test_no_fallback_returns_empty_without_model(self, monkeypatch):
        monkeypatch.setattr(db, "embed_text", lambda t: None)
        monkeypatch.setattr(db, "search_chunks", lambda q, limit=5: [{"score": 10.0}])
        assert db.vector_search("q", fallback=False) == []
        assert db.vector_search("q") == [{"score": 10.0}]
