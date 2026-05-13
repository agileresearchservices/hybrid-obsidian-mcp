"""Vault indexer - crawls Obsidian vault, generates embeddings via Ollama, bulk indexes to OpenSearch."""

import logging
import time
from hashlib import sha256
from pathlib import Path
from typing import Optional

from opensearchpy import helpers as os_helpers

from .config import OPENSEARCH_INDEX_NAME
from .embeddings import get_embeddings_batch
from .opensearch_client import create_client, ensure_index
from .vault_parser import parse_vault, ParsedNote

logger = logging.getLogger(__name__)


def make_doc_id(file_path: str) -> str:
    """Generate a stable document ID from file path."""
    return sha256(file_path.encode()).hexdigest()[:16]


def _build_embed_input(note: ParsedNote, chunk_text: str) -> str:
    tags_str = ", ".join(note.tags[:8]) if note.tags else ""
    return (
        f"Title: {note.title}\n"
        f"Type: {note.doc_type}\n"
        f"Tags: {tags_str}\n\n"
        f"{chunk_text}"
    )


def _prepare_note_docs(note: ParsedNote, vault_root: Optional[Path]) -> list[tuple[str, dict]]:
    """Return [(embed_input, doc_without_embedding), ...] for each chunk.

    The doc carries `chunk_hash` (sha256 of the embed input) for cache lookup
    on incremental reindex.
    """
    doc_id = make_doc_id(note.file_path)
    out: list[tuple[str, dict]] = []
    mtime_ms: Optional[int] = None
    if vault_root:
        abs_path = vault_root / note.file_path
        if abs_path.exists():
            mtime_ms = int(abs_path.stat().st_mtime * 1000)

    for chunk_idx, chunk_text in enumerate(note.chunks):
        embed_input = _build_embed_input(note, chunk_text)
        chunk_hash = sha256(embed_input.encode("utf-8")).hexdigest()
        doc = {
            "document_id": doc_id,
            "chunk_index": chunk_idx,
            "chunk_text": chunk_text,
            "chunk_hash": chunk_hash,
            "title": note.title,
            "tags": note.tags,
            "folder": note.folder,
            "file_path": note.file_path,
            "doc_type": note.doc_type,
        }
        if note.date and len(note.date) == 10 and note.date[4] == "-" and note.date[7] == "-":
            doc["date"] = note.date
        if mtime_ms is not None:
            doc["file_mtime"] = mtime_ms
        out.append((embed_input, doc))
    return out


def _embed_and_extend(
    prepared: list[tuple[str, dict]],
    batch_actions: list,
    cache: Optional[dict[str, list[float]]] = None,
    stats: Optional[dict] = None,
) -> int:
    """Embed prepared (input, doc) pairs and append to batch_actions.

    If `cache` is provided, docs whose `chunk_hash` is already in the cache
    skip the Ollama call entirely. `stats` (if provided) is updated with
    `cache_hits` and `cache_misses` counts.

    Returns the number of chunks added. On Ollama failure, logs and returns
    only the cache-hit count for this batch — the rest of the run continues.
    """
    if not prepared:
        return 0

    misses: list[tuple[str, dict]] = []
    added = 0
    for embed_input, doc in prepared:
        cached = cache.get(doc["chunk_hash"]) if cache else None
        if cached is not None:
            doc["embedding"] = cached
            batch_actions.append({
                "_index": OPENSEARCH_INDEX_NAME,
                "_id": f"{doc['document_id']}-{doc['chunk_index']}",
                "_source": doc,
            })
            added += 1
            if stats is not None:
                stats["cache_hits"] = stats.get("cache_hits", 0) + 1
        else:
            misses.append((embed_input, doc))

    if not misses:
        return added

    inputs = [p[0] for p in misses]
    try:
        embeddings = get_embeddings_batch(inputs, task="search_document")
    except Exception as e:
        logger.warning("Embedding batch failed (%d chunks): %s", len(inputs), e)
        return added
    for (_, doc), embedding in zip(misses, embeddings):
        doc["embedding"] = embedding
        batch_actions.append({
            "_index": OPENSEARCH_INDEX_NAME,
            "_id": f"{doc['document_id']}-{doc['chunk_index']}",
            "_source": doc,
        })
        if stats is not None:
            stats["cache_misses"] = stats.get("cache_misses", 0) + 1
    return added + len(misses)


def _load_cached_embeddings(client, file_paths: list[str]) -> dict[str, list[float]]:
    """Return {chunk_hash: embedding} for all existing chunks under these paths.

    Used before delete_by_query in incremental reindex: unchanged chunks
    short-circuit the Ollama call. Missing field / older docs simply produce
    cache misses, which is correct.
    """
    if not file_paths:
        return {}
    # Cap pulls at a generous ceiling per call; incremental batches are small.
    size_cap = max(1000, len(file_paths) * 200)
    try:
        response = client.search(
            index=OPENSEARCH_INDEX_NAME,
            body={
                "size": size_cap,
                "_source": ["chunk_hash", "embedding"],
                "query": {
                    "bool": {
                        "filter": [
                            {"terms": {"file_path": file_paths}},
                            {"exists": {"field": "chunk_hash"}},
                        ]
                    }
                },
            },
        )
    except Exception as e:
        logger.debug("Cache load failed (%s) — proceeding without cache", e)
        return {}

    cache: dict[str, list[float]] = {}
    for hit in response["hits"]["hits"]:
        src = hit["_source"]
        h = src.get("chunk_hash")
        emb = src.get("embedding")
        if h and emb:
            cache[h] = emb
    return cache


def delete_files(file_paths: list[str], client=None) -> int:
    """Delete all chunks for the given vault-relative paths. Returns count of paths processed."""
    if not file_paths:
        return 0
    client = client or create_client()
    try:
        client.delete_by_query(
            index=OPENSEARCH_INDEX_NAME,
            body={"query": {"terms": {"file_path": file_paths}}},
            refresh=True,
        )
    except Exception as e:
        logger.debug("delete_files failed for %s: %s", file_paths, e)
        return 0
    return len(file_paths)


def index_files(file_paths: list[str], vault_path: Optional[str] = None, batch_size: int = 50) -> dict:
    """Incrementally index specific files into OpenSearch.

    Deletes existing chunks for each file (by file_path), then re-parses and re-indexes
    with batched embeddings. Paths are relative to the vault root.
    """
    from pathlib import Path
    from .config import OBSIDIAN_VAULT_PATH
    from .vault_parser import parse_note

    vault_root = Path(vault_path or OBSIDIAN_VAULT_PATH)
    client = create_client()
    ensure_index(client)

    total_docs = 0
    total_chunks = 0
    errors = 0
    batch_actions: list = []
    pending: list[tuple[str, dict]] = []
    cache_stats: dict = {"cache_hits": 0, "cache_misses": 0}
    start_time = time.time()

    existing = [p for p in file_paths if (vault_root / p).exists()]
    # Load embeddings for existing chunks BEFORE deletion so we can short-circuit
    # Ollama for unchanged chunks. delete_by_query then clears stale entries
    # (handles shrinks and content changes).
    cache = _load_cached_embeddings(client, existing)
    deleted = delete_files(existing, client=client)

    for rel_path in file_paths:
        full_path = vault_root / rel_path
        if not full_path.exists():
            logger.warning("File not found: %s", full_path)
            errors += 1
            continue

        note = parse_note(full_path, vault_root)
        if not note:
            logger.warning("Could not parse: %s", rel_path)
            errors += 1
            continue

        prepared = _prepare_note_docs(note, vault_root)
        if not prepared:
            continue
        pending.extend(prepared)
        total_docs += 1

        if len(pending) >= batch_size:
            total_chunks += _embed_and_extend(pending, batch_actions, cache=cache, stats=cache_stats)
            pending = []
            if len(batch_actions) >= batch_size:
                _flush_batch(client, batch_actions)
                batch_actions.clear()

    if pending:
        total_chunks += _embed_and_extend(pending, batch_actions, cache=cache, stats=cache_stats)
    if batch_actions:
        _flush_batch(client, batch_actions)

    client.indices.refresh(index=OPENSEARCH_INDEX_NAME)
    elapsed = time.time() - start_time

    return {
        "files_requested": len(file_paths),
        "notes_indexed": total_docs,
        "chunks_indexed": total_chunks,
        "chunks_deleted": deleted,
        "cache_hits": cache_stats["cache_hits"],
        "cache_misses": cache_stats["cache_misses"],
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
    }


def index_vault(vault_path: Optional[str] = None, batch_size: int = 50) -> dict:
    """Index the entire Obsidian vault into OpenSearch.

    Returns stats dict with counts.
    """
    client = create_client()
    ensure_index(client)

    # Delete existing documents
    try:
        client.indices.delete(index=OPENSEARCH_INDEX_NAME)
        ensure_index(client)
        logger.info("Cleared existing index")
    except Exception:
        pass

    notes = parse_vault(vault_path)
    logger.info("Parsed %d notes from vault", len(notes))

    total_docs = 0
    total_chunks = 0
    errors = 0
    batch_actions: list = []
    pending: list[tuple[str, dict]] = []
    start_time = time.time()

    from .config import OBSIDIAN_VAULT_PATH
    vault_root = Path(vault_path or OBSIDIAN_VAULT_PATH)

    for i, note in enumerate(notes, 1):
        try:
            prepared = _prepare_note_docs(note, vault_root)
        except Exception as e:
            logger.warning("Error preparing %s: %s", note.file_path, e)
            errors += 1
            continue
        if prepared:
            pending.extend(prepared)
            total_docs += 1

        if len(pending) >= batch_size:
            total_chunks += _embed_and_extend(pending, batch_actions)
            pending = []
            if len(batch_actions) >= batch_size:
                _flush_batch(client, batch_actions)
                batch_actions.clear()

        if i % 10 == 0:
            print(f"  Processed {i}/{len(notes)} notes ({total_chunks} chunks)...")

    if pending:
        total_chunks += _embed_and_extend(pending, batch_actions)
    if batch_actions:
        _flush_batch(client, batch_actions)

    client.indices.refresh(index=OPENSEARCH_INDEX_NAME)
    elapsed = time.time() - start_time

    stats = {
        "notes_parsed": len(notes),
        "notes_indexed": total_docs,
        "chunks_indexed": total_chunks,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
    }
    return stats


def _flush_batch(client, actions: list) -> None:
    """Bulk index a batch of actions.

    `raise_on_error=False` keeps a single bad doc from killing the whole run,
    but we surface the per-doc reasons so silent drops don't hide. OpenSearch
    returns each failed item as `{op: {error: {type, reason, ...}, _id}}`.
    """
    try:
        success, errors = os_helpers.bulk(
            client, actions, refresh=False, raise_on_error=False
        )
        if errors:
            logger.warning("Bulk indexing had %d errors:", len(errors))
            for item in errors:
                # Each item is a single-key dict keyed by op (typically "index").
                op_result = next(iter(item.values())) if isinstance(item, dict) else {}
                doc_id = op_result.get("_id", "?")
                err = op_result.get("error") or {}
                reason = err.get("reason", str(err))
                logger.warning("  - %s: %s: %s", doc_id, err.get("type", "?"), str(reason)[:200])
    except Exception as e:
        logger.warning("Bulk indexing exception: %s", e)


def get_index_stats() -> dict:
    """Get current index statistics."""
    client = create_client()
    try:
        count = client.count(index=OPENSEARCH_INDEX_NAME)["count"]
        agg_body = {
            "size": 0,
            "aggs": {
                "doc_types": {"terms": {"field": "doc_type", "size": 20}},
                "folders": {"terms": {"field": "folder", "size": 50}},
                "unique_docs": {"cardinality": {"field": "document_id"}},
                "tags": {"terms": {"field": "tags.keyword", "size": 50}},
            },
        }
        response = client.search(index=OPENSEARCH_INDEX_NAME, body=agg_body)
        aggs = response["aggregations"]

        return {
            "total_chunks": count,
            "unique_documents": aggs["unique_docs"]["value"],
            "by_doc_type": {b["key"]: b["doc_count"] for b in aggs["doc_types"]["buckets"]},
            "by_folder": {b["key"]: b["doc_count"] for b in aggs["folders"]["buckets"]},
            "top_tags": {b["key"]: b["doc_count"] for b in aggs["tags"]["buckets"]},
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Index Obsidian vault into OpenSearch")
    parser.add_argument("--files", nargs="+", help="Specific files to index (relative to vault root)")
    args = parser.parse_args()

    if args.files:
        print(f"\nIndexing {len(args.files)} file(s)...")
        stats = index_files(args.files)
        print(f"Done! {stats['notes_indexed']} notes, {stats['chunks_indexed']} chunks in {stats['elapsed_seconds']}s")
    else:
        print("\nIndexing Obsidian vault...")
        stats = index_vault()
        print(f"Done! {stats['notes_indexed']} notes, {stats['chunks_indexed']} chunks in {stats['elapsed_seconds']}s")

    if stats["errors"]:
        print(f"  Errors: {stats['errors']}")

    print("\nIndex stats:")
    for k, v in get_index_stats().items():
        print(f"  {k}: {v}")
