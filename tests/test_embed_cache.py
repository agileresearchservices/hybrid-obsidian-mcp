"""Tests for the chunk-level embedding cache used by incremental reindex.

The cache short-circuits the Ollama call for chunks whose `chunk_hash` matches
an existing OpenSearch doc, so editing one paragraph in a long note doesn't
re-embed the unchanged chunks.
"""

from unittest.mock import MagicMock, patch

from src import indexer
from src.vault_parser import ParsedNote


def _note(chunks: list[str], file_path: str = "Notes/example.md") -> ParsedNote:
    return ParsedNote(
        file_path=file_path,
        title="Example",
        date=None,
        tags=["work"],
        folder="Notes",
        doc_type="note",
        content="\n\n".join(chunks),
        chunks=chunks,
    )


def test_prepare_note_docs_sets_chunk_hash_per_chunk():
    note = _note(["alpha chunk", "beta chunk"])
    prepared = indexer._prepare_note_docs(note, vault_root=None)
    hashes = [doc["chunk_hash"] for _, doc in prepared]
    assert len(set(hashes)) == 2
    assert all(len(h) == 64 for h in hashes)  # sha256 hex


def test_prepare_note_docs_hash_stable_across_calls():
    note = _note(["alpha chunk"])
    h1 = indexer._prepare_note_docs(note, vault_root=None)[0][1]["chunk_hash"]
    h2 = indexer._prepare_note_docs(note, vault_root=None)[0][1]["chunk_hash"]
    assert h1 == h2


def test_prepare_note_docs_hash_changes_when_chunk_text_changes():
    h_a = indexer._prepare_note_docs(_note(["alpha"]), None)[0][1]["chunk_hash"]
    h_b = indexer._prepare_note_docs(_note(["beta"]), None)[0][1]["chunk_hash"]
    assert h_a != h_b


def test_embed_and_extend_all_cache_hits_skips_ollama():
    note = _note(["alpha", "beta"])
    prepared = indexer._prepare_note_docs(note, None)
    cache = {doc["chunk_hash"]: [0.5] * 768 for _, doc in prepared}
    stats: dict = {}
    actions: list = []

    with patch.object(indexer, "get_embeddings_batch") as embed_call:
        added = indexer._embed_and_extend(prepared, actions, cache=cache, stats=stats)
        embed_call.assert_not_called()

    assert added == 2
    assert stats == {"cache_hits": 2}
    assert all(a["_source"]["embedding"] == [0.5] * 768 for a in actions)


def test_embed_and_extend_partial_cache_hits_embeds_only_misses():
    note = _note(["alpha", "beta", "gamma"])
    prepared = indexer._prepare_note_docs(note, None)
    # Only alpha is cached.
    cache = {prepared[0][1]["chunk_hash"]: [0.1] * 768}
    stats: dict = {}
    actions: list = []

    def fake_embed(inputs, task=None):
        return [[float(i)] * 768 for i in range(len(inputs))]

    with patch.object(indexer, "get_embeddings_batch", side_effect=fake_embed) as embed_call:
        added = indexer._embed_and_extend(prepared, actions, cache=cache, stats=stats)

    assert added == 3
    assert stats == {"cache_hits": 1, "cache_misses": 2}
    # Only the 2 misses were sent to Ollama, alpha was not.
    sent_inputs = embed_call.call_args.args[0]
    assert len(sent_inputs) == 2
    assert all("alpha" not in s for s in sent_inputs)


def test_embed_and_extend_no_cache_behaves_like_before():
    note = _note(["alpha"])
    prepared = indexer._prepare_note_docs(note, None)
    actions: list = []

    with patch.object(indexer, "get_embeddings_batch", return_value=[[0.9] * 768]) as embed_call:
        added = indexer._embed_and_extend(prepared, actions, cache=None)
        embed_call.assert_called_once()

    assert added == 1
    assert actions[0]["_source"]["embedding"] == [0.9] * 768


def test_load_cached_embeddings_returns_hash_to_vector_map():
    client = MagicMock()
    client.search.return_value = {
        "hits": {
            "hits": [
                {"_source": {"chunk_hash": "h1", "embedding": [0.1] * 768}},
                {"_source": {"chunk_hash": "h2", "embedding": [0.2] * 768}},
                {"_source": {"chunk_hash": None, "embedding": [0.3] * 768}},  # skipped
                {"_source": {"chunk_hash": "h4"}},  # missing embedding, skipped
            ]
        }
    }

    cache = indexer._load_cached_embeddings(client, ["Notes/a.md"])
    assert set(cache.keys()) == {"h1", "h2"}
    assert cache["h1"] == [0.1] * 768


def test_load_cached_embeddings_empty_paths_skips_query():
    client = MagicMock()
    cache = indexer._load_cached_embeddings(client, [])
    assert cache == {}
    client.search.assert_not_called()


def test_load_cached_embeddings_swallows_index_errors():
    client = MagicMock()
    client.search.side_effect = RuntimeError("index missing")
    cache = indexer._load_cached_embeddings(client, ["a.md"])
    assert cache == {}
