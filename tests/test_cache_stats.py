"""Tests for the cache_stats aggregator."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import cache_stats


@pytest.fixture
def patched_cache_infos(monkeypatch):
    """Stub each cache_info function with deterministic values."""
    emb = SimpleNamespace(hits=5, misses=2, maxsize=256, currsize=7)
    rer = SimpleNamespace(hits=10, misses=0, maxsize=1024, currsize=10)
    tax = SimpleNamespace(hits=3, misses=1, ttl_seconds=60, age_seconds=12.5, size=166)
    rn = SimpleNamespace(hits=0, misses=0, maxsize=64, currsize=0)

    monkeypatch.setattr(cache_stats.embeddings, "embedding_cache_info", MagicMock(return_value=emb))
    monkeypatch.setattr(cache_stats.reranker, "reranker_cache_info", MagicMock(return_value=rer))
    monkeypatch.setattr(cache_stats.tagger, "taxonomy_cache_info", MagicMock(return_value=tax))
    monkeypatch.setattr(cache_stats.tagger, "read_note_cache_info", MagicMock(return_value=rn))
    return emb, rer, tax, rn


def test_collect_cache_stats_includes_all_four_caches(patched_cache_infos):
    out = cache_stats.collect_cache_stats()
    assert set(out.keys()) == {"embedding_query", "reranker_scores", "taxonomy", "read_note"}


def test_collect_cache_stats_passes_values_through(patched_cache_infos):
    emb, rer, tax, rn = patched_cache_infos
    out = cache_stats.collect_cache_stats()

    assert out["embedding_query"]["hits"] == emb.hits
    assert out["embedding_query"]["misses"] == emb.misses
    assert out["embedding_query"]["maxsize"] == emb.maxsize
    assert out["embedding_query"]["currsize"] == emb.currsize

    assert out["reranker_scores"]["hits"] == rer.hits
    assert out["reranker_scores"]["currsize"] == rer.currsize

    assert out["taxonomy"]["ttl_seconds"] == tax.ttl_seconds
    assert out["taxonomy"]["age_seconds"] == tax.age_seconds
    assert out["taxonomy"]["size"] == tax.size

    assert out["read_note"]["maxsize"] == rn.maxsize


def test_hit_rate_computed_from_hits_misses(patched_cache_infos):
    out = cache_stats.collect_cache_stats()
    # 5/(5+2) = 0.7143
    assert out["embedding_query"]["hit_rate"] == pytest.approx(0.7143, abs=1e-4)
    # 10/(10+0) = 1.0
    assert out["reranker_scores"]["hit_rate"] == 1.0
    # 3/(3+1) = 0.75
    assert out["taxonomy"]["hit_rate"] == 0.75


def test_hit_rate_is_none_when_no_traffic(patched_cache_infos):
    out = cache_stats.collect_cache_stats()
    # read_note has 0/0 traffic
    assert out["read_note"]["hit_rate"] is None


def test_hit_rate_helper_handles_zero_total():
    assert cache_stats._hit_rate(0, 0) is None
    assert cache_stats._hit_rate(0, 5) == 0.0
    assert cache_stats._hit_rate(5, 0) == 1.0
    assert cache_stats._hit_rate(1, 3) == 0.25
