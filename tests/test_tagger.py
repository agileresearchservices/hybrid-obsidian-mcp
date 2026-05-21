"""Tests for the TTL-based taxonomy cache in src/tagger.py."""

from unittest.mock import patch

import pytest

from src import tagger


@pytest.fixture(autouse=True)
def _isolate_taxonomy_cache():
    tagger.clear_taxonomy_cache()
    yield
    tagger.clear_taxonomy_cache()


def _stub_uncached(side_effect=None, return_value=None):
    """Patch _collect_taxonomy_uncached and return the mock."""
    return patch.object(
        tagger,
        "_collect_taxonomy_uncached",
        side_effect=side_effect,
        return_value=return_value if side_effect is None else None,
    )


def test_first_call_misses_then_caches(monkeypatch):
    monkeypatch.setattr(tagger, "TAXONOMY_CACHE_TTL_SECONDS", 60)
    with _stub_uncached(return_value={"a": 3, "b": 1}) as m:
        tagger.collect_taxonomy()
        assert m.call_count == 1
    info = tagger.taxonomy_cache_info()
    assert info.misses == 1
    assert info.hits == 0
    assert info.size == 2


def test_second_call_within_ttl_returns_cached(monkeypatch):
    monkeypatch.setattr(tagger, "TAXONOMY_CACHE_TTL_SECONDS", 60)
    fake_clock = [1000.0]
    monkeypatch.setattr(tagger.time, "monotonic", lambda: fake_clock[0])

    with _stub_uncached(return_value={"a": 1}) as m:
        tagger.collect_taxonomy()
        fake_clock[0] += 30  # within TTL
        result = tagger.collect_taxonomy()
        assert m.call_count == 1
    assert result == {"a": 1}
    info = tagger.taxonomy_cache_info()
    assert info.hits == 1
    assert info.misses == 1


def test_call_after_ttl_recomputes(monkeypatch):
    monkeypatch.setattr(tagger, "TAXONOMY_CACHE_TTL_SECONDS", 60)
    fake_clock = [1000.0]
    monkeypatch.setattr(tagger.time, "monotonic", lambda: fake_clock[0])

    call_results = [{"a": 1}, {"a": 1, "b": 2}]
    with patch.object(tagger, "_collect_taxonomy_uncached", side_effect=call_results) as m:
        first = tagger.collect_taxonomy()
        fake_clock[0] += 61  # past TTL
        second = tagger.collect_taxonomy()
        assert m.call_count == 2
    assert first == {"a": 1}
    assert second == {"a": 1, "b": 2}


def test_zero_ttl_bypasses_cache(monkeypatch):
    monkeypatch.setattr(tagger, "TAXONOMY_CACHE_TTL_SECONDS", 0)
    with patch.object(tagger, "_collect_taxonomy_uncached", return_value={"a": 1}) as m:
        tagger.collect_taxonomy()
        tagger.collect_taxonomy()
        tagger.collect_taxonomy()
        assert m.call_count == 3
    info = tagger.taxonomy_cache_info()
    assert info.hits == 0
    assert info.misses == 3
    assert info.size == 0  # nothing cached


def test_clear_forces_recompute(monkeypatch):
    monkeypatch.setattr(tagger, "TAXONOMY_CACHE_TTL_SECONDS", 60)
    with patch.object(tagger, "_collect_taxonomy_uncached", return_value={"a": 1}) as m:
        tagger.collect_taxonomy()
        tagger.clear_taxonomy_cache()
        tagger.collect_taxonomy()
        assert m.call_count == 2
    # clear resets counters too
    info = tagger.taxonomy_cache_info()
    assert info.misses == 1
    assert info.hits == 0


def test_cache_info_reflects_state(monkeypatch):
    monkeypatch.setattr(tagger, "TAXONOMY_CACHE_TTL_SECONDS", 60)
    fake_clock = [1000.0]
    monkeypatch.setattr(tagger.time, "monotonic", lambda: fake_clock[0])

    info = tagger.taxonomy_cache_info()
    assert info.size == 0
    assert info.age_seconds is None

    with patch.object(tagger, "_collect_taxonomy_uncached", return_value={"a": 1, "b": 2}):
        tagger.collect_taxonomy()
    fake_clock[0] += 5
    info = tagger.taxonomy_cache_info()
    assert info.size == 2
    assert info.age_seconds == pytest.approx(5)
    assert info.ttl_seconds == 60


def test_collect_taxonomy_top_k_uses_cache(monkeypatch):
    """top_k delegates to collect_taxonomy, so a second call should still hit cache."""
    monkeypatch.setattr(tagger, "TAXONOMY_CACHE_TTL_SECONDS", 60)
    with patch.object(tagger, "_collect_taxonomy_uncached", return_value={"a": 5, "b": 3, "c": 1}) as m:
        tagger.collect_taxonomy_top_k(2)
        tagger.collect_taxonomy_top_k(3)
        assert m.call_count == 1
