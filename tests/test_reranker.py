"""Tests for LocalReranker score caching."""

from unittest.mock import MagicMock

import pytest

from src import reranker as reranker_mod
from src.reranker import LocalReranker, ScoredChunk


@pytest.fixture
def fake_model():
    """Stub the cross-encoder predict() so tests don't load the real model."""
    m = MagicMock()
    # Default: return ascending scores so order matches input order.
    m.predict.side_effect = lambda pairs: [float(i) for i in range(len(pairs))]
    return m


@pytest.fixture
def rr(fake_model):
    r = LocalReranker()
    r._model = fake_model  # bypass _ensure_model
    return r


def _chunk(text: str, chash: str = None) -> dict:
    d = {"chunk_text": text}
    if chash is not None:
        d["chunk_hash"] = chash
    return d


def test_cold_miss_predicts_all_chunks(rr, fake_model):
    chunks = [_chunk("a", "h1"), _chunk("b", "h2"), _chunk("c", "h3")]
    rr.rerank("query", chunks, top_k=3)

    fake_model.predict.assert_called_once()
    pairs = fake_model.predict.call_args.args[0]
    assert [p[1] for p in pairs] == ["a", "b", "c"]
    info = rr._cache.info()
    assert info.misses == 3
    assert info.currsize == 3


def test_warm_hit_skips_model_entirely(rr, fake_model):
    chunks = [_chunk("a", "h1"), _chunk("b", "h2")]
    rr.rerank("same query", chunks, top_k=2)
    fake_model.predict.reset_mock()

    rr.rerank("same query", chunks, top_k=2)
    fake_model.predict.assert_not_called()
    info = rr._cache.info()
    assert info.hits == 2


def test_partial_hit_predicts_only_misses(rr, fake_model):
    rr.rerank("q", [_chunk("a", "h1"), _chunk("b", "h2")], top_k=2)
    fake_model.predict.reset_mock()
    fake_model.predict.side_effect = lambda pairs: [99.0] * len(pairs)

    # Second rerank has 1 cached (h1) + 2 new (h3, h4)
    rr.rerank("q", [_chunk("a", "h1"), _chunk("c", "h3"), _chunk("d", "h4")], top_k=3)

    fake_model.predict.assert_called_once()
    pairs = fake_model.predict.call_args.args[0]
    # Only the misses get passed to predict
    assert sorted(p[1] for p in pairs) == ["c", "d"]


def test_different_query_isolates_cache(rr, fake_model):
    rr.rerank("alpha", [_chunk("a", "h1")], top_k=1)
    fake_model.predict.reset_mock()

    # Same chunk_hash, different query — must NOT hit
    rr.rerank("beta", [_chunk("a", "h1")], top_k=1)
    fake_model.predict.assert_called_once()


def test_chunks_without_chunk_hash_pass_through_uncached(rr, fake_model):
    chunks = [_chunk("a"), _chunk("b")]  # no chunk_hash on either
    rr.rerank("q", chunks, top_k=2)
    fake_model.predict.reset_mock()
    fake_model.predict.side_effect = lambda pairs: [7.0] * len(pairs)

    rr.rerank("q", chunks, top_k=2)
    # No chunk_hash means no cache write, no cache read — both calls predict everything
    fake_model.predict.assert_called_once()
    info = rr._cache.info()
    assert info.currsize == 0


def test_returned_scored_chunks_are_sorted_descending(rr, fake_model):
    fake_model.predict.side_effect = lambda pairs: [0.1, 0.9, 0.5]
    chunks = [_chunk("a", "h1"), _chunk("b", "h2"), _chunk("c", "h3")]
    result = rr.rerank("q", chunks, top_k=3)
    assert [r.score for r in result] == [0.9, 0.5, 0.1]
    assert [r.chunk_text for r in result] == ["b", "c", "a"]


def test_top_k_truncates_after_sort(rr, fake_model):
    fake_model.predict.side_effect = lambda pairs: [0.1, 0.9, 0.5]
    chunks = [_chunk("a", "h1"), _chunk("b", "h2"), _chunk("c", "h3")]
    result = rr.rerank("q", chunks, top_k=2)
    assert len(result) == 2
    assert result[0].score == 0.9
    assert result[1].score == 0.5


def test_clear_reranker_cache(rr, fake_model):
    rr.rerank("q", [_chunk("a", "h1")], top_k=1)
    assert rr._cache.info().currsize == 1
    rr._cache.clear()
    info = rr._cache.info()
    assert info.currsize == 0
    assert info.hits == 0
    assert info.misses == 0


def test_empty_chunks_returns_empty(rr, fake_model):
    assert rr.rerank("q", [], top_k=5) == []
    fake_model.predict.assert_not_called()


def test_module_level_helpers_against_singleton(monkeypatch, fake_model):
    """clear_reranker_cache / reranker_cache_info forward to the singleton."""
    monkeypatch.setattr(reranker_mod, "_reranker", None)
    info = reranker_mod.reranker_cache_info()
    assert info.currsize == 0
    assert info.hits == 0  # no singleton yet, defaults returned

    # Force-instantiate via get_reranker, inject fake model, exercise it
    r = reranker_mod.get_reranker()
    r._model = fake_model
    r.rerank("q", [_chunk("a", "h1")], top_k=1)
    assert reranker_mod.reranker_cache_info().currsize == 1

    reranker_mod.clear_reranker_cache()
    assert reranker_mod.reranker_cache_info().currsize == 0


def test_disabled_reranking_bypasses_cache_and_model(monkeypatch, fake_model):
    monkeypatch.setattr(reranker_mod, "ENABLE_RERANKING", False)
    r = LocalReranker()
    r._model = fake_model
    chunks = [{"chunk_text": "a", "chunk_hash": "h1", "_score": 0.7}]
    result = r.rerank("q", chunks, top_k=1)
    assert result[0].score == 0.7
    fake_model.predict.assert_not_called()
    assert r._cache.info().currsize == 0


def test_cache_lru_eviction_when_full():
    """Once cache exceeds maxsize, oldest entries are evicted."""
    from src.reranker import _BoundedScoreCache

    cache = _BoundedScoreCache(maxsize=2)
    cache.put(("q", "h1"), 0.1)
    cache.put(("q", "h2"), 0.2)
    cache.put(("q", "h3"), 0.3)  # evicts h1
    assert cache.get(("q", "h1")) is None
    assert cache.get(("q", "h2")) == 0.2
    assert cache.get(("q", "h3")) == 0.3
    assert cache.info().currsize == 2


def test_cache_disabled_when_maxsize_zero():
    from src.reranker import _BoundedScoreCache

    cache = _BoundedScoreCache(maxsize=0)
    cache.put(("q", "h1"), 0.1)
    assert cache.get(("q", "h1")) is None
    assert cache.info().currsize == 0
