"""Shared test setup."""

import os

import pytest

# Never read or write the on-disk corpus cache from tests.
os.environ["ACG_CACHE_DIR"] = ""


@pytest.fixture(autouse=True)
def _fresh_corpus_cache():
    from src.acg.corpus import reset_corpus

    reset_corpus()
    yield
    reset_corpus()
