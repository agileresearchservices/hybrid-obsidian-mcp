"""Tests for wikilink normalization, parsing, and graph_neighbors traversal."""

from unittest.mock import MagicMock, patch

import pytest

from src import searcher
from src.vault_parser import (
    ParsedNote,
    extract_wiki_links,
    normalize_wikilink,
)


def test_normalize_strips_section_anchor():
    assert normalize_wikilink("KMW#Setup") == "kmw"


def test_normalize_lowercases_and_collapses_whitespace():
    assert normalize_wikilink("  Hyrule  Project  ") == "hyrule project"


def test_normalize_empty_target():
    assert normalize_wikilink("#section-only") == ""


def test_extract_wiki_links_with_alias():
    text = "see [[KMW|the company]] and [[Hyrule Project]]."
    assert extract_wiki_links(text) == ["KMW", "Hyrule Project"]


def test_parsed_note_default_wikilinks_is_empty():
    n = ParsedNote(
        file_path="a.md", title="A", date=None, tags=[], folder="",
        doc_type="note", content="x", chunks=["x"],
    )
    assert n.wikilinks == []


def _fake_search_response(notes: list[dict]) -> dict:
    return {"hits": {"hits": [{"_source": n} for n in notes]}}


def test_graph_neighbors_hops_must_be_1_or_2():
    with pytest.raises(ValueError):
        searcher.graph_neighbors("target", hops=3)


def test_graph_neighbors_empty_target_returns_empty():
    assert searcher.graph_neighbors("   ") == []


def test_graph_neighbors_hop1_returns_direct_linkers():
    client = MagicMock()
    client.search.return_value = _fake_search_response([
        {"title": "Daily 2026-05-12", "file_path": "Daily Log/2026-05-12.md",
         "wikilinks": ["kmw", "lucille"]},
        {"title": "Standup", "file_path": "KMW/Standup.md", "wikilinks": ["kmw"]},
    ])
    with patch.object(searcher, "create_client", return_value=client):
        results = searcher.graph_neighbors("KMW", hops=1)

    assert len(results) == 2
    assert all(n["hop_distance"] == 1 for n in results)
    # The term query should use the normalized target.
    call_body = client.search.call_args.kwargs["body"]
    assert call_body["query"] == {"term": {"wikilinks": "kmw"}}


def test_graph_neighbors_hop2_expands_via_outgoing_links():
    client = MagicMock()
    # Hop 1: returns one note that links to kmw and also to lucille + hyrule
    hop1_response = _fake_search_response([
        {"title": "Daily", "file_path": "Daily Log/a.md",
         "wikilinks": ["kmw", "lucille", "hyrule"]},
    ])
    # Hop 2: returns notes linking to lucille / hyrule
    hop2_response = _fake_search_response([
        {"title": "Lucille Notes", "file_path": "KMW/Lucille.md"},
        {"title": "Hyrule Doc", "file_path": "KMW/Hyrule.md"},
    ])
    client.search.side_effect = [hop1_response, hop2_response]

    with patch.object(searcher, "create_client", return_value=client):
        results = searcher.graph_neighbors("KMW", hops=2)

    distances = [n["hop_distance"] for n in results]
    assert distances == [1, 2, 2]
    # The hop-2 query should be a terms query over the outgoing links, excluding
    # the original target.
    hop2_call_body = client.search.call_args_list[1].kwargs["body"]
    terms = set(hop2_call_body["query"]["terms"]["wikilinks"])
    assert terms == {"lucille", "hyrule"}
    assert "kmw" not in terms


def test_graph_neighbors_hop2_with_no_outgoing_returns_hop1_only():
    client = MagicMock()
    client.search.return_value = _fake_search_response([
        {"title": "Leaf", "file_path": "leaf.md", "wikilinks": []},
    ])
    with patch.object(searcher, "create_client", return_value=client):
        results = searcher.graph_neighbors("KMW", hops=2)
    assert len(results) == 1
    assert results[0]["hop_distance"] == 1


def test_graph_neighbors_hop2_dedupes_paths_already_in_hop1():
    client = MagicMock()
    hop1_response = _fake_search_response([
        {"title": "A", "file_path": "a.md", "wikilinks": ["other"]},
    ])
    # Hop 2 returns the same path again somehow — should be deduped
    hop2_response = _fake_search_response([
        {"title": "A again", "file_path": "a.md"},
        {"title": "B", "file_path": "b.md"},
    ])
    client.search.side_effect = [hop1_response, hop2_response]
    with patch.object(searcher, "create_client", return_value=client):
        results = searcher.graph_neighbors("KMW", hops=2)
    paths = [n["file_path"] for n in results]
    assert paths == ["a.md", "b.md"]
