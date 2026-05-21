"""Tests for the TTL-based taxonomy cache and read_note LRU in src/tagger.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

from src import tagger


@pytest.fixture(autouse=True)
def _isolate_taxonomy_cache():
    tagger.clear_taxonomy_cache()
    tagger.clear_read_note_cache()
    yield
    tagger.clear_taxonomy_cache()
    tagger.clear_read_note_cache()


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


# ---------------------------------------------------------------------------
# read_note LRU
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_note(tmp_path, monkeypatch):
    """Point tagger.VAULT at a tmp dir and create a sample note. Yields the rel path."""
    monkeypatch.setattr(tagger, "VAULT", tmp_path)
    note = tmp_path / "sample.md"
    note.write_text("hello world", encoding="utf-8")
    yield "sample.md", note


def test_read_note_returns_content(vault_note, monkeypatch):
    monkeypatch.setattr(tagger, "READ_NOTE_CACHE_SIZE", 64)
    rel, _ = vault_note
    assert tagger.read_note(rel) == "hello world"


def test_read_note_caches_within_same_mtime(vault_note, monkeypatch):
    monkeypatch.setattr(tagger, "READ_NOTE_CACHE_SIZE", 64)
    rel, path = vault_note
    tagger.read_note(rel)
    # Mutate the underlying file's content but force mtime to stay the same
    # (simulating same-mtime read — cache should serve stale)
    original_mtime_ns = path.stat().st_mtime_ns
    path.write_bytes(b"different content")
    import os
    os.utime(path, ns=(original_mtime_ns, original_mtime_ns))

    cached = tagger.read_note(rel)
    assert cached == "hello world"  # served from cache
    info = tagger.read_note_cache_info()
    assert info.hits == 1
    assert info.misses == 1


def test_read_note_reloads_when_mtime_changes(vault_note, monkeypatch):
    monkeypatch.setattr(tagger, "READ_NOTE_CACHE_SIZE", 64)
    rel, path = vault_note
    tagger.read_note(rel)

    # Edit + bump mtime
    path.write_text("v2 content", encoding="utf-8")
    import os
    new_mtime_ns = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(new_mtime_ns, new_mtime_ns))

    assert tagger.read_note(rel) == "v2 content"
    info = tagger.read_note_cache_info()
    # Two misses, no hits — old key abandoned, new key inserted
    assert info.misses == 2


def test_read_note_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(tagger, "VAULT", tmp_path)
    monkeypatch.setattr(tagger, "READ_NOTE_CACHE_SIZE", 64)
    assert tagger.read_note("does/not/exist.md") is None
    assert tagger.read_note_cache_info().misses == 0


def test_read_note_disabled_when_cache_size_zero(vault_note, monkeypatch):
    monkeypatch.setattr(tagger, "READ_NOTE_CACHE_SIZE", 0)
    rel, _ = vault_note
    tagger.read_note(rel)
    tagger.read_note(rel)
    info = tagger.read_note_cache_info()
    assert info.hits == 0
    assert info.misses == 0  # bypass path doesn't increment counters
    assert info.currsize == 0


def test_read_note_lru_evicts_oldest(monkeypatch, tmp_path):
    monkeypatch.setattr(tagger, "VAULT", tmp_path)
    monkeypatch.setattr(tagger, "READ_NOTE_CACHE_SIZE", 2)
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    tagger.read_note("a.md")
    tagger.read_note("b.md")
    tagger.read_note("c.md")  # evicts a.md
    info = tagger.read_note_cache_info()
    assert info.currsize == 2
    # Reading a.md again should be a miss
    tagger.read_note("a.md")
    info = tagger.read_note_cache_info()
    assert info.misses == 4


def test_clear_read_note_cache(vault_note, monkeypatch):
    monkeypatch.setattr(tagger, "READ_NOTE_CACHE_SIZE", 64)
    rel, _ = vault_note
    tagger.read_note(rel)
    assert tagger.read_note_cache_info().currsize == 1
    tagger.clear_read_note_cache()
    info = tagger.read_note_cache_info()
    assert info.currsize == 0
    assert info.hits == 0
    assert info.misses == 0
