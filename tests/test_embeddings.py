"""Tests for the shared Ollama embeddings client."""

from unittest.mock import patch, MagicMock

import httpx
import pytest

from src import embeddings


def _fake_response(payload: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def test_get_embedding_applies_query_prefix():
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return _fake_response({"embeddings": [[0.1, 0.2, 0.3]]})

    with patch.object(embeddings.httpx, "post", side_effect=fake_post):
        vec = embeddings.get_embedding("hello world", task="search_query")

    assert vec == [0.1, 0.2, 0.3]
    assert captured["json"]["input"] == "search_query: hello world"


def test_get_embeddings_batch_applies_document_prefix_and_chunks():
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return _fake_response({"embeddings": [[float(i)] for i in range(len(json["input"]))]})

    texts = [f"note {i}" for i in range(70)]
    with patch.object(embeddings.httpx, "post", side_effect=fake_post):
        vecs = embeddings.get_embeddings_batch(texts, task="search_document", batch_size=32)

    assert len(vecs) == 70
    # Three calls: 32 + 32 + 6
    assert [len(c["input"]) for c in calls] == [32, 32, 6]
    # Prefix applied to every input
    for c in calls:
        for s in c["input"]:
            assert s.startswith("search_document: ")


def test_empty_batch_returns_empty_list_without_calling_ollama():
    with patch.object(embeddings.httpx, "post") as p:
        assert embeddings.get_embeddings_batch([]) == []
        p.assert_not_called()


def test_retry_on_timeout_then_success():
    attempts = {"n": 0}

    def fake_post(url, json, timeout):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise httpx.ConnectTimeout("boom")
        return _fake_response({"embeddings": [[0.9]]})

    with patch.object(embeddings.httpx, "post", side_effect=fake_post):
        vec = embeddings.get_embedding("retryable")

    assert vec == [[0.9][0]] or vec == [0.9]
    assert attempts["n"] == 2


def test_retry_gives_up_after_three_attempts():
    attempts = {"n": 0}

    def fake_post(url, json, timeout):
        attempts["n"] += 1
        raise httpx.ConnectTimeout("nope")

    with patch.object(embeddings.httpx, "post", side_effect=fake_post):
        with pytest.raises(httpx.ConnectTimeout):
            embeddings.get_embedding("dead")
    assert attempts["n"] == 3


def test_mismatched_embedding_count_raises():
    def fake_post(url, json, timeout):
        return _fake_response({"embeddings": [[0.1]]})  # 1 vec for 3 inputs

    with patch.object(embeddings.httpx, "post", side_effect=fake_post):
        with pytest.raises(embeddings.EmbeddingError):
            embeddings.get_embeddings_batch(["a", "b", "c"], batch_size=32)
