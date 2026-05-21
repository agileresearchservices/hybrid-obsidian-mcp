"""Tests for MCP server startup helpers."""

from unittest.mock import MagicMock

import pytest

from src import server


@pytest.fixture
def fake_reranker(monkeypatch):
    """Stub get_reranker() to return a MagicMock without loading the real model."""
    rr = MagicMock()
    monkeypatch.setattr(server, "get_reranker", lambda: rr)
    return rr


def test_prewarm_loads_model_when_enabled(fake_reranker, monkeypatch):
    monkeypatch.setattr(server, "RERANKER_PREWARM", True)
    monkeypatch.setattr(server, "ENABLE_RERANKING", True)

    loaded = server._prewarm_reranker_if_enabled()

    assert loaded is True
    fake_reranker._ensure_model.assert_called_once()


def test_prewarm_skipped_when_RERANKER_PREWARM_false(fake_reranker, monkeypatch):
    monkeypatch.setattr(server, "RERANKER_PREWARM", False)
    monkeypatch.setattr(server, "ENABLE_RERANKING", True)

    loaded = server._prewarm_reranker_if_enabled()

    assert loaded is False
    fake_reranker._ensure_model.assert_not_called()


def test_prewarm_skipped_when_reranking_disabled(fake_reranker, monkeypatch):
    monkeypatch.setattr(server, "RERANKER_PREWARM", True)
    monkeypatch.setattr(server, "ENABLE_RERANKING", False)

    loaded = server._prewarm_reranker_if_enabled()

    assert loaded is False
    fake_reranker._ensure_model.assert_not_called()


def test_prewarm_swallows_model_load_errors(fake_reranker, monkeypatch):
    """A failing prewarm must not crash startup — the first search will retry."""
    monkeypatch.setattr(server, "RERANKER_PREWARM", True)
    monkeypatch.setattr(server, "ENABLE_RERANKING", True)
    fake_reranker._ensure_model.side_effect = RuntimeError("model server down")

    loaded = server._prewarm_reranker_if_enabled()

    assert loaded is False  # error path returns False instead of raising
