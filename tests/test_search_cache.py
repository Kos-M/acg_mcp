"""Tests for the corpus cache, keyword/vector search over it, confidence
consistency, content extraction and non-blocking tools.

Background (issue report, 2026-11-21): every acg_* call took >120 s against
a remote MongoDB. vector_search pulled every embedding per query plus one
find_one per chunk; sync tools blocked the event loop so parallel calls
queued; GitHub page furniture was indexed; code blocks were moved away from
their prose; and off-topic matches were labelled MEDIUM with an instruction
that contradicted ``answerable: false``.

MongoDB is faked — no live instance or embedding model required.
"""

import asyncio
import json
import sys
import threading
import time

sys.path.insert(0, ".")

import pytest

import src.acg.db as db
from src.acg import confidence
from src.acg.corpus import get_corpus
from src.acg.indexer import extract_text_from_html, split_into_sentences, group_into_chunks


# ---------------------------------------------------------------------------
# Fake MongoDB (only what Corpus.sync uses)
# ---------------------------------------------------------------------------

class FakeSources:
    def __init__(self):
        self.docs = {}
        self.find_count = 0

    def find(self, filt=None, projection=None):
        self.find_count += 1
        return [dict(d) for d in self.docs.values()]

    def aggregate(self, pipeline):
        if not self.docs:
            return []
        return [{"_id": None, "n": len(self.docs), "latest": max(d["indexed_at"] for d in self.docs.values())}]


class FakeData:
    def __init__(self):
        self.docs = []
        self.find_calls = []

    def estimated_document_count(self):
        return len(self.docs)

    def find(self, filt=None, projection=None):
        wanted = set(filt["source_id"]["$in"])
        self.find_calls.append(sorted(wanted))
        return [dict(d) for d in self.docs if d["source_id"] in wanted]


class FakeDB:
    def __init__(self):
        self.sources = FakeSources()
        self.data = FakeData()

    def add_source(self, url_hash, url, chunks, version="v1"):
        """chunks: list of (text, embedding-or-None)."""
        self.data.docs = [d for d in self.data.docs if d["source_id"] != url_hash]
        for i, (text, emb) in enumerate(chunks):
            doc = {
                "source_id": url_hash, "chunk_index": i,
                "css_selector": f"#acg-chunk-{url_hash}-{i}", "shi_prefix": f"shi{url_hash}",
                "sentences": [text], "text": text,
            }
            if emb is not None:
                doc["embedding"] = emb
            self.data.docs.append(doc)
        self.sources.docs[url_hash] = {
            "url_hash": url_hash, "url": url, "indexed_at": version,
            "shi_prefix": f"shi{url_hash}", "total_chunks": len(chunks),
        }

    def remove_source(self, url_hash):
        self.sources.docs.pop(url_hash, None)
        self.data.docs = [d for d in self.data.docs if d["source_id"] != url_hash]


@pytest.fixture
def fake_db(monkeypatch):
    fdb = FakeDB()
    monkeypatch.setattr(db, "get_db", lambda: fdb)
    return fdb


# ---------------------------------------------------------------------------
# Corpus cache sync
# ---------------------------------------------------------------------------

class TestCorpusSync:
    def test_only_new_or_changed_sources_are_fetched(self, fake_db):
        fake_db.add_source("aaaa", "https://a", [("alpha text", [1.0, 0.0])])
        fake_db.add_source("bbbb", "https://b", [("beta text", [0.0, 1.0])])
        db._synced_corpus()
        assert fake_db.data.find_calls == [["aaaa", "bbbb"]]

        db._synced_corpus()  # nothing changed -> no chunk fetch
        assert len(fake_db.data.find_calls) == 1

        fake_db.add_source("bbbb", "https://b", [("beta v2", [0.0, 1.0])], version="v2")
        corpus = db._synced_corpus()
        assert fake_db.data.find_calls[-1] == ["bbbb"]
        assert sorted(c["text"] for c in corpus.chunks) == ["alpha text", "beta v2"]

    def test_unchanged_database_skips_source_scan(self, fake_db):
        fake_db.add_source("aaaa", "https://a", [("alpha", [1.0, 0.0])])
        db._synced_corpus()
        scans = fake_db.sources.find_count
        db._synced_corpus()
        db._synced_corpus()
        assert fake_db.sources.find_count == scans, "no change -> only the cheap signature check"
        fake_db.add_source("bbbb", "https://b", [("beta", [0.0, 1.0])], version="v2")
        assert len(db._synced_corpus().chunks) == 2

    def test_removed_source_dropped(self, fake_db):
        fake_db.add_source("aaaa", "https://a", [("alpha", [1.0, 0.0])])
        fake_db.add_source("bbbb", "https://b", [("beta", [0.0, 1.0])])
        db._synced_corpus()
        fake_db.remove_source("aaaa")
        corpus = db._synced_corpus()
        assert [c["url"] for c in corpus.chunks] == ["https://b"]

    def test_partially_written_source_is_reloaded(self, fake_db):
        fake_db.add_source("aaaa", "https://a", [("one", [1.0, 0.0]), ("two", [1.0, 0.0])])
        fake_db.data.docs.pop()  # chunk 1 not written yet
        assert len(db._synced_corpus().chunks) == 1
        fake_db.add_source("aaaa", "https://a", [("one", [1.0, 0.0]), ("two", [1.0, 0.0])])
        assert len(db._synced_corpus().chunks) == 2

    def test_disk_cache_round_trip(self, fake_db, tmp_path, monkeypatch):
        from src.acg import corpus as corpus_mod

        monkeypatch.setenv("ACG_CACHE_DIR", str(tmp_path))
        corpus_mod._corpus = None
        fake_db.add_source("aaaa", "https://a", [("alpha", [1.0, 0.0]), ("beta", None)])
        db._synced_corpus()
        assert len(fake_db.data.find_calls) == 1

        corpus_mod._corpus = None  # simulate a server restart
        corpus = db._synced_corpus()
        assert len(fake_db.data.find_calls) == 1, "restart must load from disk, not MongoDB"
        assert [c["text"] for c in corpus.chunks] == ["alpha", "beta"]
        assert corpus.matrix.shape == (2, 2)


# ---------------------------------------------------------------------------
# Keyword search
# ---------------------------------------------------------------------------

class TestKeywordSearch:
    def test_tokenize_query_drops_stopwords(self):
        assert db._tokenize_query("How to stream a chat completion") == ["stream", "chat", "completion"]

    def test_empty_tokens_returns_empty(self, fake_db):
        assert db.search_chunks("the and of", limit=5) == []

    def test_matches_non_verbatim_query(self, fake_db):
        fake_db.add_source("aaaa", "https://example.com/1", [("How to stream a chat completion with the API.", None)])
        fake_db.add_source("bbbb", "https://example.com/2", [("The documentation covers installation only.", None)])
        urls = [r["url"] for r in db.search_chunks("chat for streaming", limit=5)]
        assert urls == ["https://example.com/1"]

    def test_full_coverage_ranks_first(self, fake_db):
        fake_db.add_source("aaaa", "https://partial", [("chat streaming is covered later in the guide", None)])
        fake_db.add_source("bbbb", "https://full", [("chat streaming completion tokens explained", None)])
        results = db.search_chunks("chat streaming completion", limit=5)
        assert results[0]["url"] == "https://full"
        assert results[0]["keyword_coverage"] == 1.0

    def test_short_tokens_match_whole_words_only(self, fake_db):
        # "ai" used to match "maintain", "email", "detail"...
        fake_db.add_source("aaaa", "https://a", [("We maintain email details here.", None)])
        assert db.search_chunks("ai", limit=5) == []

    def test_long_tokens_match_word_prefix(self, fake_db):
        fake_db.add_source("aaaa", "https://a", [("Coding agents can edit files.", None)])
        assert [r["url"] for r in db.search_chunks("agent", limit=5)] == ["https://a"]

    def test_rare_terms_outweigh_common_terms(self, fake_db):
        # "code"/"agents" appear everywhere; "wordpress" only once. A chunk
        # with the rare term must beat one with only the common terms.
        common = [(f"code agents tutorial {i}", None) for i in range(20)]
        fake_db.add_source("cccc", "https://common", common)
        fake_db.add_source("wwww", "https://wp", [("wordpress plugin guide", None)])
        results = db.search_chunks("wordpress code agents", limit=3)
        assert results[0]["url"] == "https://wp"
        assert results[1]["keyword_coverage"] < 0.5

    def test_no_candidates(self, fake_db):
        fake_db.add_source("aaaa", "https://a", [("unrelated", None)])
        assert db.search_chunks("nonexistenttermxyz", limit=5) == []


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------

class TestVectorSearch:
    def test_scans_whole_corpus_no_candidate_cap(self, fake_db, monkeypatch):
        monkeypatch.setattr(db, "embed_text", lambda t: [1.0, 0.0])
        chunks = [("x", [0.0, 1.0])] * 300
        chunks[250] = ("hot", [1.0, 0.0])
        fake_db.add_source("aaaa", "https://a", chunks)
        results = db.vector_search("q", limit=5, min_score=0.9)
        assert [r["chunk_index"] for r in results] == [250]
        assert results[0]["url"] == "https://a"
        assert results[0]["score"] == 1.0

    def test_sorted_and_thresholded(self, fake_db, monkeypatch):
        monkeypatch.setattr(db, "embed_text", lambda t: [1.0, 0.0])
        fake_db.add_source("aaaa", "https://a", [("a", [1.0, 1.0]), ("b", [1.0, 0.1]), ("c", [0.0, 1.0])])
        results = db.vector_search("q", limit=5, min_score=0.5)
        assert [r["text"] for r in results] == ["b", "a"]

    def test_chunks_without_embedding_are_skipped(self, fake_db, monkeypatch):
        monkeypatch.setattr(db, "embed_text", lambda t: [1.0, 0.0])
        fake_db.add_source("aaaa", "https://a", [("a", None), ("b", [1.0, 0.0])])
        assert [r["text"] for r in db.vector_search("q", min_score=0.5)] == ["b"]

    def test_no_model_falls_back_or_returns_empty(self, fake_db, monkeypatch):
        monkeypatch.setattr(db, "embed_text", lambda t: [])
        fake_db.add_source("aaaa", "https://a", [("chat streaming", None)])
        assert db.vector_search("chat", fallback=False) == []
        assert [r["url"] for r in db.vector_search("chat")] == ["https://a"]


# ---------------------------------------------------------------------------
# Confidence: tier, answerable and instruction always agree
# ---------------------------------------------------------------------------

class TestConfidence:
    @pytest.mark.parametrize("hybrid,tier", [(0.85, "HIGH"), (0.54, "MEDIUM"), (0.38, "LOW"), (0.0, "LOW")])
    def test_tiers(self, hybrid, tier):
        assert confidence.assess([{"hybrid_score": hybrid, "score": 0.9}])["tier"] == tier

    def test_off_topic_vector_score_does_not_inflate_confidence(self):
        # Report: off-topic pages with cosine ~0.72 came back MEDIUM.
        result = {"hybrid_score": 0.38, "score": 0.72, "vector_score": 0.72}
        verdict = confidence.assess([result])
        assert verdict["tier"] == "LOW" and verdict["answerable"] is False

    @pytest.mark.parametrize("hybrid", [0.1, 0.45, 0.5, 0.69, 0.7, 0.95])
    def test_answerable_and_instruction_consistent(self, hybrid):
        verdict = confidence.assess([{"hybrid_score": hybrid}])
        says_no_fetch = "Do not use web_fetch" in verdict["instruction"]
        assert verdict["answerable"] == (verdict["tier"] != "LOW")
        assert says_no_fetch == (verdict["tier"] == "HIGH")
        if not verdict["answerable"]:
            assert "Use web_fetch" in verdict["instruction"]

    def test_legacy_keyword_scale(self):
        assert confidence.compute_confidence([{"score": 9.0}]) == 0.9

    def test_check_indexed_tool_is_consistent(self, monkeypatch):
        import src.tools.indexer_tools as it
        from mcp.server.fastmcp import FastMCP

        monkeypatch.setattr(it, "_search_sources", lambda q, limit=5: [
            {"url": "https://x", "chunk_index": 0, "text": "t", "hybrid_score": 0.4, "score": 0.72},
        ])
        monkeypatch.setattr(it, "count_sources", lambda: 470)
        monkeypatch.setattr(it, "get_neighbour_chunks", lambda u, i, n: [])
        mcp = FastMCP("t")
        it.register_tools(mcp)
        out = asyncio.run(mcp.call_tool("acg_check_indexed", {"query": "q"}))
        payload = json.loads(out[0][0].text if isinstance(out, tuple) else out[0].text)
        assert payload["tier"] == "LOW"
        assert payload["answerable"] is False
        assert "Do not use web_fetch" not in payload["instruction"]


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

GITHUB_LIKE = """
<html><body>
<header><a>Sign in</a><a>Sign up</a></header>
<nav><a>Product</a><a>Solutions</a></nav>
<main>
  <div class="file-list">src/ tests/ README.md 12 commits</div>
  <div class="Layout-sidebar"><aside>Stars 120 Watchers 9 Forks 30 Report repository</aside></div>
  <article class="markdown-body">
    <h1>MCP Adapter</h1>
    <p>The adapter exposes WordPress abilities as MCP tools for AI agents.</p>
    <p>Install it with Composer and register an ability. The adapter then serves it over STDIO or HTTP.</p>
    <p>Each ability declares an input schema and a permission callback, so clients only see what the current user can do.</p>
    <p>Configuration examples for Claude Desktop and other MCP clients are in the CLI usage guide.</p>
  </article>
</main>
<footer>Used by 40 Contributors 12</footer>
</body></html>
"""

DOCS_WITH_CODE = """
<html><body><main><article>
<h1>Authentication</h1>
<p>Cookie authentication is the standard method. Nonces protect against CSRF.</p>
<p>This is how the built-in client creates the nonce:</p>
<pre><code>wp_localize_script( 'wp-api', 'wpApiSettings', array(
  'nonce' => wp_create_nonce( 'wp_rest' ) ) );</code></pre>
<p>The nonce is then sent in the X-WP-Nonce header. Use <code>wp_create_nonce</code> on the server.</p>
<table><tr><th>Header</th><th>Purpose</th><th>Required</th></tr>
<tr><td>X-WP-Nonce</td><td>CSRF token</td><td>yes</td></tr></table>
<p>Application passwords are covered next.</p>
</article></main></body></html>
"""


class TestExtraction:
    def test_github_readme_only(self):
        text = extract_text_from_html(GITHUB_LIKE)
        assert "exposes WordPress abilities" in text
        for clutter in ("Sign in", "Product", "Stars 120", "12 commits", "Used by"):
            assert clutter not in text, clutter

    def test_code_block_stays_with_its_prose(self):
        text = extract_text_from_html(DOCS_WITH_CODE)
        intro = text.index("creates the nonce")
        code = text.index("[CODE]: wp_localize_script")
        after = text.index("X-WP-Nonce header")
        assert intro < code < after
        assert not text.startswith("[CODE]")

    def test_inline_code_kept_in_sentence(self):
        text = extract_text_from_html(DOCS_WITH_CODE)
        assert "wp_create_nonce" in text[text.index("X-WP-Nonce header"):]

    def test_table_stays_in_place(self):
        text = extract_text_from_html(DOCS_WITH_CODE)
        assert text.index("X-WP-Nonce header") < text.index("X-WP-Nonce | CSRF token | yes") < text.index("Application passwords")

    def test_code_block_is_one_segment(self):
        text = extract_text_from_html(DOCS_WITH_CODE)
        segs = split_into_sentences(text)
        code = [s for s in segs if s.startswith("[CODE]:")]
        assert len(code) == 1 and "wp_create_nonce" in code[0]

    def test_duplicate_paragraphs_dropped(self):
        html = "<html><body><main>" + "<p>Repeated sidebar block.</p><p>Real content here.</p>" * 3 + \
               "<p>" + "Filler sentence. " * 100 + "</p></main></body></html>"
        text = extract_text_from_html(html)
        assert text.count("Repeated sidebar block.") == 1
        assert text.count("Real content here.") == 1

    def test_page_without_main_container(self):
        html = "<html><body><header><a>Logo</a></header><p>Hello <b>world</b>!</p></body></html>"
        text = extract_text_from_html(html)
        assert "Hello" in text and "world" in text and "Logo" not in text

    def test_pipe_in_code_not_treated_as_table(self):
        chunks = group_into_chunks(["Intro.", "[CODE]: a | b | c", "Outro."], sentences_per_chunk=5)
        assert chunks == [["Intro.", "[CODE]: a | b | c", "Outro."]]


# ---------------------------------------------------------------------------
# Tools must not block the event loop
# ---------------------------------------------------------------------------

class TestToolsRunInThreads:
    def test_parallel_sync_tools_overlap(self):
        from mcp.server.fastmcp import FastMCP
        from src.tools import run_sync_tools_in_threads

        mcp = FastMCP("t")
        run_sync_tools_in_threads(mcp)
        running = {"now": 0, "max": 0}
        lock = threading.Lock()

        @mcp.tool(name="slow")
        def slow(x: int) -> str:
            with lock:
                running["now"] += 1
                running["max"] = max(running["max"], running["now"])
            time.sleep(0.3)
            with lock:
                running["now"] -= 1
            return str(x)

        async def main():
            start = time.perf_counter()
            await asyncio.gather(*(mcp.call_tool("slow", {"x": i}) for i in range(4)))
            return time.perf_counter() - start

        elapsed = asyncio.run(main())
        assert running["max"] == 4, "tool calls must run concurrently"
        assert elapsed < 1.0
        assert slow(7) == "7", "decorated function stays directly callable"


class TestExtractorVersioning:
    def test_legacy_sources_use_legacy_extractor(self):
        # Sources indexed before versioning must be re-extracted the old way,
        # or their SHI no longer matches and every claim fails verification.
        from src.acg import indexer

        assert indexer.extract_text_for_source(DOCS_WITH_CODE, None) == indexer._extract_text_legacy(DOCS_WITH_CODE)
        assert indexer.extract_text_for_source(DOCS_WITH_CODE, 1) == indexer._extract_text_legacy(DOCS_WITH_CODE)
        assert indexer.extract_text_for_source(DOCS_WITH_CODE, 2) == indexer.extract_text_from_html(DOCS_WITH_CODE)
        assert indexer._extract_text_legacy(DOCS_WITH_CODE) != indexer.extract_text_from_html(DOCS_WITH_CODE)
