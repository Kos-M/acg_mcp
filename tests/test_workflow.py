"""Tests for the enforced ACG workflow (single-call pipeline).

The workflow composes the individual ACG tools into one audited pipeline:
search -> ground -> verify -> audit. These tests mock the I/O-heavy pieces
(search_sources, index_url, verify_claims) so the orchestration logic is
tested in isolation from MongoDB/network.
"""

import sys

sys.path.insert(0, ".")

from src.acg.workflow import (
    run_workflow,
    _compose_grounded_answer,
    _build_chunk_signatures,
    _compute_confidence,
    format_audit_footer,
)


# ---------------------------------------------------------------------------
# _compose_grounded_answer
# ---------------------------------------------------------------------------

class TestComposeGroundedAnswer:
    def test_composes_claims_with_markers(self):
        results = [
            {
                "url": "https://example.com/a",
                "shi_prefix": "abc123def456",
                "css_selector": "#acg-chunk-aa-0",
                "text": "The sky is blue and the sun is bright.",
                "sentences": ["The sky is blue and the sun is bright."],
                "score": 9.5,
            },
            {
                "url": "https://example.com/b",
                "shi_prefix": "def456abc123",
                "css_selector": "#acg-chunk-bb-0",
                "text": "Water freezes at zero degrees.",
                "sentences": ["Water freezes at zero degrees."],
                "score": 8.0,
            },
        ]
        grounded = _compose_grounded_answer(results)
        assert "[C1:abc123def456:css=#acg-chunk-aa-0]" in grounded
        assert "[C2:def456abc123:css=#acg-chunk-bb-0]" in grounded
        assert "The sky is blue" in grounded
        assert "Water freezes" in grounded

    def test_empty_results(self):
        assert _compose_grounded_answer([]) == ""

    def test_max_claims_cap(self):
        results = [
            {
                "url": f"https://example.com/{i}",
                "shi_prefix": f"p{i:012d}",
                "css_selector": f"#c{i}",
                "text": f"Claim {i}.",
                "sentences": [f"Claim {i}."],
            }
            for i in range(6)
        ]
        grounded = _compose_grounded_answer(results, max_claims=3)
        assert grounded.count("[C") == 3


# ---------------------------------------------------------------------------
# _compute_confidence
# ---------------------------------------------------------------------------

class TestComputeConfidence:
    def test_high_confidence_from_keyword_score(self):
        assert _compute_confidence([{"score": 9.0}]) >= 0.7

    def test_empty_results_zero(self):
        assert _compute_confidence([]) == 0.0


# ---------------------------------------------------------------------------
# _build_chunk_signatures
# ---------------------------------------------------------------------------

class TestBuildChunkSignatures:
    def test_builds_table_entries(self):
        sigs = _build_chunk_signatures(
            [
                {
                    "claim_id": "C1",
                    "source_url": "https://x.com/a",
                    "shi_prefix": "abc123def456",
                    "verified": True,
                }
            ]
        )
        assert sigs[0]["claim_id"] == "C1"
        assert sigs[0]["source_url"] == "https://x.com/a"
        assert sigs[0]["verified"] is True


# ---------------------------------------------------------------------------
# format_audit_footer
# ---------------------------------------------------------------------------

class TestFormatAuditFooter:
    def test_footer_contains_audit_blocks(self):
        report = {
            "chunk_signatures": [
                {"claim_id": "C1", "shi_prefix": "abc123def456", "source_url": "https://x.com", "verified": True}
            ],
            "claims_verified": "1/1",
            "acg_accuracy": 100.0,
            "acg_signed": "ACG Protocol",
        }
        footer = format_audit_footer(report)
        assert "Chunk Signatures Table" in footer
        assert "[Claims Verified: 1/1]" in footer
        assert "[ACG Accuracy: 100.0%]" in footer
        assert "[ACG Signed: ACG Protocol]" in footer
        assert "abc123def456" in footer


# ---------------------------------------------------------------------------
# run_workflow
# ---------------------------------------------------------------------------

_RESULT = {
    "url": "https://example.com/doc",
    "shi_prefix": "abc123def456",
    "css_selector": "#acg-chunk-aa-0",
    "text": "The sky is blue and the sun is bright.",
    "sentences": ["The sky is blue and the sun is bright."],
    "score": 9.0,
}

_VERIFY_OK = {
    "verified": True,
    "total_claims": 1,
    "passed": 1,
    "failed": 0,
    "results": [
        {
            "claim_id": "C1",
            "shi_prefix": "abc123def456",
            "source_url": "https://example.com/doc",
            "verified": True,
        }
    ],
}


class TestRunWorkflow:
    def test_low_confidence_report_when_no_sources(self, monkeypatch):
        monkeypatch.setattr("src.acg.indexer.search_sources", lambda q, limit=5: [])
        report = run_workflow("something unknown")
        assert report["confidence_tier"] == "LOW"
        assert report["answerable"] is False
        assert report["grounded_answer"] == ""
        assert report["workflow"] == ["search", "ground", "verify", "audit"]

    def test_indexes_url_when_low_confidence(self, monkeypatch):
        calls = {"n": 0}

        def fake_search(q, limit=5):
            calls["n"] += 1
            return [] if calls["n"] == 1 else [_RESULT]

        monkeypatch.setattr("src.acg.indexer.search_sources", fake_search)
        monkeypatch.setattr(
            "src.acg.indexer.index_url",
            lambda url, sentences_per_chunk=8: {
                "url": url,
                "shi_prefix": "abc123def456",
                "total_chunks": 2,
            },
        )
        monkeypatch.setattr("src.acg.verifier.verify_claims", lambda text: _VERIFY_OK)

        report = run_workflow("sky color", url="https://example.com/doc")
        assert calls["n"] == 2  # search -> index -> search again
        assert report["indexed_url"] == "https://example.com/doc"
        assert report["confidence_tier"] == "HIGH"
        assert "sky" in report["grounded_answer"].lower()

    def test_full_report_format(self, monkeypatch):
        monkeypatch.setattr("src.acg.indexer.search_sources", lambda q, limit=5: [_RESULT])
        monkeypatch.setattr("src.acg.verifier.verify_claims", lambda text: _VERIFY_OK)

        report = run_workflow("sky color")
        assert report["workflow"] == ["search", "ground", "verify", "audit"]
        assert report["confidence_tier"] == "HIGH"
        assert report["claims_verified"] == "1/1"
        assert report["acg_accuracy"] == 100.0
        assert report["acg_signed"] == "ACG Protocol"
        assert len(report["chunk_signatures"]) == 1
        assert report["var"]["protocol"] == "ACG/1.0"
        assert len(report["var"]["ssr_entries"]) == 1

    def test_accuracy_reflects_failed_claims(self, monkeypatch):
        monkeypatch.setattr("src.acg.indexer.search_sources", lambda q, limit=5: [_RESULT])
        verify_fail = {
            "verified": False,
            "total_claims": 2,
            "passed": 1,
            "failed": 1,
            "results": [
                {"claim_id": "C1", "shi_prefix": "abc123def456", "source_url": "https://example.com/doc", "verified": True},
                {"claim_id": "C2", "shi_prefix": "111111111111", "source_url": "https://other.com", "verified": False},
            ],
        }
        monkeypatch.setattr("src.acg.verifier.verify_claims", lambda text: verify_fail)
        report = run_workflow("sky color")
        assert report["claims_verified"] == "1/2"
        assert report["acg_accuracy"] == 50.0
