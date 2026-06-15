"""Tests for ACG MCP tools.

These tests verify the core ACG protocol logic and tool registrations.
MongoDB-dependent tests require a running MongoDB instance.
"""

import json
import sys
import pytest

sys.path.insert(0, ".")

from src.acg.shi import generate_shi, generate_shi_full, verify_shi
from src.acg.ugvp import make_claim_marker, parse_claim_markers, make_ssr_entry, CLAIM_MARKER_PATTERN
from src.acg.rsvp import make_relationship_marker, parse_relationship_markers, make_rar_entry, RELATIONSHIP_TYPES
from src.acg.var import build_var, var_to_json, var_from_json
from src.acg.indexer import split_into_sentences, group_into_chunks, generate_css_selector, extract_text_from_html
from src.acg.spider import is_same_domain, extract_links


# ---------------------------------------------------------------------------
# SHI tests
# ---------------------------------------------------------------------------

class TestSHI:
    def test_generate_shi(self):
        shi = generate_shi("hello world")
        assert len(shi) == 12
        assert shi == "b94d27b9934d"

    def test_generate_shi_empty_raises(self):
        with pytest.raises(ValueError):
            generate_shi("")

    def test_generate_shi_full(self):
        full = generate_shi_full("hello world")
        assert len(full) == 64

    def test_verify_shi_match(self):
        content = "test content"
        shi = generate_shi(content)
        assert verify_shi(content, shi) is True

    def test_verify_shi_no_match(self):
        shi = generate_shi("content a")
        assert verify_shi("content b", shi) is False


# ---------------------------------------------------------------------------
# UGVP tests
# ---------------------------------------------------------------------------

class TestUGVP:
    def test_make_claim_marker(self):
        marker = make_claim_marker(1, "abc123def456", "#test")
        assert marker == "[C1:abc123def456:css=#test]"

    def test_parse_claim_markers(self):
        text = "The sky is blue [C1:abc123def456:css=#para1]. Water is wet [C2:def456abc123:css=#para2]."
        markers = parse_claim_markers(text)
        assert len(markers) == 2
        assert markers[0]["claim_id"] == 1
        assert markers[0]["shi_prefix"] == "abc123def456"
        assert markers[1]["css_selector"] == "#para2"

    def test_no_claim_markers(self):
        text = "No markers here."
        assert parse_claim_markers(text) == []

    def test_make_ssr_entry(self):
        ssr = make_ssr_entry(1, "abc123def456", "https://example.com", "#p1", "Test claim", False)
        assert ssr["claim_id"] == "C1"
        assert ssr["verified"] is False


# ---------------------------------------------------------------------------
# RSVP tests
# ---------------------------------------------------------------------------

class TestRSVP:
    def test_make_relationship_marker(self):
        marker = make_relationship_marker(1, "SUMMARY", ["C1", "C2"])
        assert marker == "(R1:SUMMARY:C1,C2)"

    def test_make_relationship_marker_invalid_type(self):
        with pytest.raises(ValueError):
            make_relationship_marker(1, "INVALID", ["C1"])

    def test_parse_relationship_markers(self):
        text = "Overall summary (R1:SUMMARY:C1,C2). But there's a contradiction (R2:CONTRADICTION:C1,C3)."
        markers = parse_relationship_markers(text)
        assert len(markers) == 2
        assert markers[0]["rel_type"] == "SUMMARY"
        assert markers[1]["claim_ids"] == ["C1", "C3"]

    def test_all_relationship_types_defined(self):
        assert "SUMMARY" in RELATIONSHIP_TYPES
        assert "CONTRADICTION" in RELATIONSHIP_TYPES
        assert "EXTENSION" in RELATIONSHIP_TYPES
        assert "CAUSAL" in RELATIONSHIP_TYPES
        assert "ANALOGY" in RELATIONSHIP_TYPES

    def test_make_rar_entry(self):
        rar = make_rar_entry(1, "SUMMARY", ["C1", "C2"], "Synthesis text")
        assert rar["relationship_id"] == "R1"


# ---------------------------------------------------------------------------
# VAR tests
# ---------------------------------------------------------------------------

class TestVAR:
    def test_build_var_empty(self):
        var = build_var([], [])
        assert var["protocol"] == "ACG/1.0"
        assert var["ssr_entries"] == []
        assert var["rar_entries"] == []

    def test_build_var_with_entries(self):
        ssr = [make_ssr_entry(1, "abc", "https://x.com", "#p1", "claim", False)]
        rar = [make_rar_entry(1, "SUMMARY", ["C1"], "text")]
        var = build_var(ssr, rar)
        assert len(var["ssr_entries"]) == 1
        assert len(var["rar_entries"]) == 1

    def test_var_json_roundtrip(self):
        var = build_var([], [])
        json_str = var_to_json(var)
        parsed = var_from_json(json_str)
        assert parsed["protocol"] == "ACG/1.0"


# ---------------------------------------------------------------------------
# Indexer tests
# ---------------------------------------------------------------------------

class TestIndexer:
    def test_split_into_sentences(self):
        sentences = split_into_sentences("Hello world. This is a test. Another sentence!")
        assert len(sentences) == 3

    def test_split_empty_text(self):
        assert split_into_sentences("") == []

    def test_group_into_chunks(self):
        sentences = ["S1.", "S2.", "S3.", "S4.", "S5.", "S6.", "S7.", "S8.", "S9.", "S10."]
        chunks = group_into_chunks(sentences, sentences_per_chunk=5)
        assert len(chunks) == 2
        assert len(chunks[0]) == 5

    def test_generate_css_selector(self):
        selector = generate_css_selector(0, "https://example.com")
        assert selector.startswith("#acg-chunk-")

    def test_extract_text_from_html(self):
        html = "<html><body><p>Hello <b>world</b>!</p></body></html>"
        text = extract_text_from_html(html)
        assert "Hello" in text
        assert "world" in text


# ---------------------------------------------------------------------------
# Spider tests
# ---------------------------------------------------------------------------

class TestSpider:
    def test_is_same_domain(self):
        assert is_same_domain("https://example.com/page1", "https://example.com/page2")
        assert is_same_domain("https://www.example.com", "https://example.com")
        assert not is_same_domain("https://example.com", "https://other.com")

    def test_extract_links_no_html(self):
        assert extract_links("", "https://example.com") == []

    def test_extract_links_basic(self):
        html = '<html><body><a href="/page1">Link</a></body></html>'
        links = extract_links(html, "https://example.com")
        assert "https://example.com/page1" in links

    def test_extract_links_skip_javascript(self):
        html = '<html><body><a href="javascript:void(0)">Bad</a></body></html>'
        links = extract_links(html, "https://example.com")
        assert len(links) == 0


# ---------------------------------------------------------------------------
# Server registration tests
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_all_tools_registered(self):
        """Verify all 12 tools are registered on the FastMCP server."""
        import asyncio
        from src.server import create_server

        mcp = create_server()

        async def check():
            tools = await mcp.list_tools()
            names = {t.name for t in tools}
            expected = {
                "acg_index_url", "acg_check_indexed", "acg_search_sources",
                "acg_list_sources", "acg_count_sources", "acg_reset_database",
                "acg_generate_grounded_text", "acg_verify_claims", "acg_build_var",
                "acg_crawl_and_index", "acg_crawl_status", "acg_crawl_list_tasks",
            }
            assert names == expected, f"Missing tools: {expected - names}"
            assert len(tools) == 12

        asyncio.run(check())
