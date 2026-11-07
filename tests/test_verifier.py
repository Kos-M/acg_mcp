"""Regression tests for issue #6 — verifier false positives and claim bleed.

The false-control claims come straight from the issue report: each points
at a real indexed chunk but contradicts it, and must NOT be verified.

All network/MongoDB access is mocked.
"""

import sys

sys.path.insert(0, ".")

import pytest

import src.acg.verifier as verifier
from src.acg.verifier import match_claim, _extract_claim_texts, verify_claims


QWEN_CHUNK = (
    "Qwen3-32B has the following features: Type: Causal Language Models. "
    "Training Stage: Pretraining & Post-training. Number of Parameters: 32.8B. "
    "Number of Paramaters (Non-Embedding): 31.2B. Number of Layers: 64. "
    "Number of Attention Heads (GQA): 64 for Q and 8 for KV. "
    "Context Length: 32,768 natively and 131,072 tokens with YaRN."
)

ASUS_CHUNK = (
    "Expansion Slots: AMD Ryzen 7000 Series Desktop Processors. "
    "2 x PCIe 4.0 x16 slots (support x16 or x8/x8 modes). "
    "AMD B650 Chipset: 1 x PCIe 4.0 x16 slot (supports x2 mode)."
)

VLLM_CHUNK = (
    "Requirements: OS: Linux. Python: 3.9 - 3.12. "
    "GPU: compute capability 7.0 or higher (e.g., V100, T4, RTX20xx, A100, L4, H100, etc.)."
)

PAGE_TITLE = "Qwen/Qwen3-32B · Hugging Face"


# ---------------------------------------------------------------------------
# match_claim — false controls must fail
# ---------------------------------------------------------------------------

class TestFalseControls:
    def test_wrong_layer_count_is_contradicted(self):
        r = match_claim("Number of Layers: 96", QWEN_CHUNK)
        assert r["status"] == "contradicted"
        assert "96" in r["missing_numbers"]

    def test_wrong_pcie_slots_is_contradicted(self):
        r = match_claim(
            "The motherboard has 4 x PCIe 5.0 x16 slots running x16/x16/x16/x16",
            ASUS_CHUNK,
        )
        assert r["status"] == "contradicted"
        assert "5.0" in r["missing_numbers"]

    def test_mutually_exclusive_compute_capability(self):
        good = match_claim("GPU: compute capability 7.0 or higher", VLLM_CHUNK)
        bad = match_claim("GPU: compute capability 8.0 or higher", VLLM_CHUNK)
        assert good["status"] == "exact"
        assert bad["status"] == "contradicted"

    def test_number_prefix_does_not_exact_match(self):
        # "Layers: 6" is a substring of "Layers: 64" but must not match.
        r = match_claim("Number of Layers: 6", QWEN_CHUNK)
        assert r["status"] == "contradicted"

    def test_number_elsewhere_in_page_does_not_help(self):
        # 96 appears on the page, but not in the chunk the marker points at.
        r = match_claim("Number of Layers: 96", QWEN_CHUNK, source_text=QWEN_CHUNK + " Released 1996. 96 GPUs")
        assert r["status"] == "contradicted"


# ---------------------------------------------------------------------------
# match_claim — true claims must still pass
# ---------------------------------------------------------------------------

class TestTrueClaims:
    def test_exact_claim(self):
        assert match_claim("Number of Layers: 64", QWEN_CHUNK)["status"] == "exact"

    def test_exact_ignores_case_and_trailing_period(self):
        assert match_claim("number of layers: 64.", QWEN_CHUNK)["status"] == "exact"

    def test_paraphrase_is_supported(self):
        r = match_claim("Qwen3-32B has 64 layers", QWEN_CHUNK)
        assert r["status"] == "supported"
        assert r["similarity"] >= verifier.SUPPORTED_THRESHOLD

    def test_thousands_separator_normalised(self):
        r = match_claim("Qwen3-32B context length is 32768 tokens natively", QWEN_CHUNK)
        assert r["status"] in ("supported", "exact")

    def test_correct_pcie_claim_supported(self):
        r = match_claim("The motherboard has 2 x PCIe 4.0 x16 slots", ASUS_CHUNK)
        assert r["status"] == "supported"

    def test_identifier_found_elsewhere_in_source(self):
        # Model name only in the page title, not the chunk: still allowed.
        chunk = "Number of Layers: 64. Number of Attention Heads (GQA): 64 for Q and 8 for KV."
        r = match_claim("Qwen3-32B has 64 layers", chunk, source_text=PAGE_TITLE + "\n" + chunk)
        assert r["status"] in ("supported", "partial")
        assert r["status"] != "contradicted"

    def test_exact_across_chunk_boundary(self):
        chunk = "Number of Layers:"
        window = "Training Stage: Pretraining. Number of Layers: 64. Heads: 64."
        assert match_claim("Number of Layers: 64", chunk, window_text=window)["status"] == "exact"

    def test_unrelated_claim_not_found(self):
        r = match_claim("The sky is blue and grass is green", QWEN_CHUNK)
        assert r["status"] == "not_found"


# ---------------------------------------------------------------------------
# _extract_claim_texts — no bleed across markers (issue #6 part 2)
# ---------------------------------------------------------------------------

M1 = "[C1:aaaaaaaaaaaa:css=#acg-chunk-11111111-0]"
M2 = "[C2:aaaaaaaaaaaa:css=#acg-chunk-11111111-1]"
M3 = "[C3:aaaaaaaaaaaa:css=#acg-chunk-11111111-2]"


class TestClaimExtraction:
    def test_newline_separated_claims(self):
        text = f"Claim A {M1}\nClaim B {M2}\nClaim C {M3}"
        assert _extract_claim_texts(text) == ["Claim A", "Claim B", "Claim C"]

    def test_same_line_claims_split_at_previous_marker(self):
        text = f"Claim A {M1} Claim B {M2}"
        assert _extract_claim_texts(text) == ["Claim A", "Claim B"]

    def test_relationship_marker_is_a_boundary(self):
        text = f"Claim A {M1}\nSummary (R1:SUMMARY:C1) Claim B {M2}"
        assert _extract_claim_texts(text) == ["Claim A", "Claim B"]

    def test_last_sentence_kept(self):
        text = f"Intro sentence here. The actual claim {M1}"
        assert _extract_claim_texts(text) == ["The actual claim"]

    def test_marker_without_text(self):
        assert _extract_claim_texts(M1) == [None]

    def test_duplicate_claim_ids_get_own_text(self):
        text = f"First {M1}\nSecond {M1}"
        assert _extract_claim_texts(text) == ["First", "Second"]

    def test_twelve_claims_no_bleed(self):
        lines = [f"Fact number {i} [C{i}:aaaaaaaaaaaa:css=#acg-chunk-11111111-{i}]" for i in range(1, 13)]
        texts = _extract_claim_texts("\n".join(lines))
        assert texts == [f"Fact number {i}" for i in range(1, 13)]


# ---------------------------------------------------------------------------
# verify_claims — end to end with mocked I/O
# ---------------------------------------------------------------------------

SHI = "5b24df715221"
URL_HASH = "cc640439"


@pytest.fixture
def mocked_io(monkeypatch):
    chunks = {
        12: {"chunk_index": 12, "text": "Qwen3 highlights: thinking mode and non-thinking mode."},
        13: {"chunk_index": 13, "text": QWEN_CHUNK},
        14: {"chunk_index": 14, "text": "Quickstart: use the latest transformers."},
        20: {"chunk_index": 20, "text": "Citation: if you find our work helpful, feel free to cite us."},
    }
    saved = []

    def fake_window(source_id, idx, radius=1):
        if source_id != URL_HASH or idx not in chunks:
            return []
        return [chunks[i] for i in range(idx - radius, idx + radius + 1) if i in chunks]

    monkeypatch.setattr(verifier, "get_source", lambda shi: (
        {"url": "https://huggingface.co/Qwen/Qwen3-32B", "shi_prefix": SHI, "url_hash": URL_HASH}
        if shi == SHI else None
    ))
    monkeypatch.setattr(verifier, "get_chunk_window", fake_window)
    monkeypatch.setattr(verifier, "fetch_url", lambda url: "<html></html>")
    monkeypatch.setattr(verifier, "extract_text_from_html", lambda html: PAGE_TITLE + "\n" + QWEN_CHUNK)
    monkeypatch.setattr(verifier, "verify_shi", lambda text, shi: True)
    monkeypatch.setattr(verifier, "save_claim", lambda d: saved.append(d) or True)
    return saved


def _marker(n, idx=13, url_hash=URL_HASH, shi=SHI):
    return f"[C{n}:{shi}:css=#acg-chunk-{url_hash}-{idx}]"


class TestVerifyClaims:
    def test_issue_repro_false_claim_not_verified(self, mocked_io):
        report = verify_claims(f"Number of Layers: 96 {_marker(1)}")
        r = report["results"][0]
        assert r["verified"] is False
        assert r["status"] == "contradicted"
        assert report["passed"] == 0

    def test_true_claim_verified(self, mocked_io):
        report = verify_claims(f"Number of Layers: 64 {_marker(1)}")
        r = report["results"][0]
        assert r["verified"] is True
        assert r["status"] == "exact"
        assert r["similarity"] == 1.0

    def test_mixed_multi_claim(self, mocked_io):
        text = "\n".join([
            f"Number of Layers: 64 {_marker(1)}",
            f"Number of Layers: 96 {_marker(2)}",
            f"Qwen3-32B has 64 layers {_marker(3)}",
        ])
        report = verify_claims(text)
        statuses = [r["status"] for r in report["results"]]
        texts = [r["claim_text"] for r in report["results"]]
        assert statuses == ["exact", "contradicted", "supported"]
        assert texts == ["Number of Layers: 64", "Number of Layers: 96", "Qwen3-32B has 64 layers"]
        assert report["passed"] == 2 and report["failed"] == 1
        assert report["verified"] is False

    def test_claim_in_other_chunk_not_verified(self, mocked_io):
        # True fact, but the marker points at a distant, unrelated chunk.
        report = verify_claims(f"Number of Layers: 64 {_marker(1, idx=20)}")
        assert report["results"][0]["verified"] is False

    def test_auto_selector_is_unlocated(self, mocked_io):
        report = verify_claims(f"Number of Layers: 64 [C1:{SHI}:css=#acg-auto]")
        r = report["results"][0]
        assert r["status"] == "unlocated"
        assert r["verified"] is False

    def test_selector_from_other_source_is_unlocated(self, mocked_io):
        report = verify_claims(f"Number of Layers: 64 {_marker(1, url_hash='deadbeef')}")
        assert report["results"][0]["status"] == "unlocated"

    def test_shi_mismatch_not_verified(self, mocked_io, monkeypatch):
        monkeypatch.setattr(verifier, "verify_shi", lambda text, shi: False)
        report = verify_claims(f"Number of Layers: 64 {_marker(1)}")
        r = report["results"][0]
        assert r["verified"] is False
        assert "SHI" in r["reason"]

    def test_unknown_source(self, mocked_io):
        report = verify_claims("Something [C1:000000000000:css=#acg-chunk-cc640439-13]")
        assert report["results"][0]["status"] == "unlocated"

    def test_saved_claim_carries_status(self, mocked_io):
        verify_claims(f"Number of Layers: 96 {_marker(1)}")
        assert mocked_io[0]["status"] == "contradicted"
        assert mocked_io[0]["verified"] is False
