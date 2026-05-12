"""Tests for exclude_tags and recency-decay query shaping in searcher.

These exercise the query-builder helpers and the body OpenSearch would receive
without actually hitting the cluster.
"""

import json
from unittest.mock import MagicMock, patch

from src import searcher


def test_build_filters_collects_must_and_must_not():
    must, must_not = searcher._build_filters(
        tags=["nasuni"],
        date_from="2026-01-01",
        date_to=None,
        folder="KMW",
        exclude_tags=["archived", "draft"],
    )
    assert {"terms": {"tags.keyword": ["nasuni"]}} in must
    assert {"range": {"date": {"gte": "2026-01-01"}}} in must
    assert {"prefix": {"folder": "KMW"}} in must
    assert must_not == [{"terms": {"tags.keyword": ["archived", "draft"]}}]


def test_build_filters_empty_inputs():
    must, must_not = searcher._build_filters(None, None, None, None, None)
    assert must == []
    assert must_not == []


def test_apply_recency_decay_disabled_passes_through(monkeypatch):
    monkeypatch.setattr(searcher, "RECENCY_DECAY_ENABLED", False)
    q = {"match_all": {}}
    assert searcher._apply_recency_decay(q) is q


def test_apply_recency_decay_zero_weight_passes_through(monkeypatch):
    monkeypatch.setattr(searcher, "RECENCY_DECAY_ENABLED", True)
    monkeypatch.setattr(searcher, "RECENCY_DECAY_WEIGHT", 0.0)
    q = {"match_all": {}}
    assert searcher._apply_recency_decay(q) is q


def test_apply_recency_decay_wraps_query(monkeypatch):
    monkeypatch.setattr(searcher, "RECENCY_DECAY_ENABLED", True)
    monkeypatch.setattr(searcher, "RECENCY_DECAY_WEIGHT", 0.3)
    monkeypatch.setattr(searcher, "RECENCY_DECAY_SCALE", "90d")
    inner = {"multi_match": {"query": "x"}}
    wrapped = searcher._apply_recency_decay(inner)
    assert wrapped["function_score"]["query"] is inner
    fn = wrapped["function_score"]["functions"][0]
    assert fn["gauss"]["file_mtime"]["scale"] == "90d"
    assert fn["weight"] == 0.3
    assert wrapped["function_score"]["boost_mode"] == "multiply"


def _fake_client_capturing_body() -> tuple[MagicMock, dict]:
    captured: dict = {}
    client = MagicMock()

    def fake_search(*, index, body, **kw):
        captured["index"] = index
        captured["body"] = body
        captured["params"] = kw.get("params")
        return {"hits": {"hits": []}}

    client.search.side_effect = fake_search
    return client, captured


def test_hybrid_search_threads_exclude_tags_into_both_sides(monkeypatch):
    monkeypatch.setattr(searcher, "RECENCY_DECAY_ENABLED", False)
    client, captured = _fake_client_capturing_body()
    with patch.object(searcher, "create_client", return_value=client), \
         patch.object(searcher, "get_embedding", return_value=[0.1] * 768):
        searcher.hybrid_search("hello", exclude_tags=["archived"], rerank=False)

    body_str = json.dumps(captured["body"])
    # exclude_tags should appear in both the kNN filter (as negated bool) and
    # the BM25 must_not.
    assert '"must_not": [{"terms": {"tags.keyword": ["archived"]}}]' in body_str
    # kNN filter wraps must_not in bool
    knn_filter = captured["body"]["query"]["hybrid"]["queries"][0]["knn"]["embedding"]["filter"]
    assert {"bool": {"must_not": [{"terms": {"tags.keyword": ["archived"]}}]}} in knn_filter["bool"]["must"]


def test_hybrid_search_applies_decay_to_bm25(monkeypatch):
    monkeypatch.setattr(searcher, "RECENCY_DECAY_ENABLED", True)
    monkeypatch.setattr(searcher, "RECENCY_DECAY_WEIGHT", 0.3)
    monkeypatch.setattr(searcher, "RECENCY_DECAY_SCALE", "90d")
    client, captured = _fake_client_capturing_body()
    with patch.object(searcher, "create_client", return_value=client), \
         patch.object(searcher, "get_embedding", return_value=[0.1] * 768):
        searcher.hybrid_search("hello", rerank=False)

    # No filters → BM25 sub-query is the bare function_score
    bm25 = captured["body"]["query"]["hybrid"]["queries"][1]
    assert "function_score" in bm25
    assert bm25["function_score"]["functions"][0]["gauss"]["file_mtime"]["scale"] == "90d"


def test_hybrid_search_decay_nests_inside_filter_bool(monkeypatch):
    monkeypatch.setattr(searcher, "RECENCY_DECAY_ENABLED", True)
    monkeypatch.setattr(searcher, "RECENCY_DECAY_WEIGHT", 0.3)
    client, captured = _fake_client_capturing_body()
    with patch.object(searcher, "create_client", return_value=client), \
         patch.object(searcher, "get_embedding", return_value=[0.1] * 768):
        searcher.hybrid_search("hello", tags=["nasuni"], rerank=False)

    bm25 = captured["body"]["query"]["hybrid"]["queries"][1]
    # With filters the BM25 side is wrapped in bool with must=[function_score], filter, must_not
    assert "bool" in bm25
    assert "function_score" in bm25["bool"]["must"][0]
    assert {"terms": {"tags.keyword": ["nasuni"]}} in bm25["bool"]["filter"]


def test_list_notes_with_exclude_tags(monkeypatch):
    client, captured = _fake_client_capturing_body()
    with patch.object(searcher, "create_client", return_value=client):
        searcher.list_notes(exclude_tags=["archived"])

    q = captured["body"]["query"]
    assert q["bool"]["must_not"] == [{"terms": {"tags.keyword": ["archived"]}}]
