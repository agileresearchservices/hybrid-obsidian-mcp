"""Vault indexer - crawls Obsidian vault, generates embeddings via Ollama, bulk indexes to OpenSearch."""

import logging
import sys
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

# Notes to accumulate before flushing embeddings + bulk-index. Tuned to keep
# Ollama happy (~32 chunks per request) while still amortizing HTTP overhead.
EMBED_BATCH_NOTES = 16


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
    """Return [(embed_input, doc_without_embedding), ...] for each chunk."""
    doc_id = make_doc_id(note.file_path)
    out: list[tuple[str, dict]] = []
    mtime_ms: Optional[int] = None
    if vault_root:
        abs_path = vault_root / note.file_path
        if abs_path.exists():
            mtime_ms = int(abs_path.stat().st_mtime * 1000)

    for chunk_idx, chunk_text in enumerate(note.chunks):
        doc = {
            "document_id": doc_id,
            "chunk_index": chunk_idx,
            "chunk_text": chunk_text,
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
        out.append((_build_embed_input(note, chunk_text), doc))
    return out


def _embed_and_extend(prepared: list[tuple[str, dict]], batch_actions: list) -> int:
    """Embed prepared (input, doc) pairs as one Ollama call and append to batch_actions.

    Returns the number of chunks added. On Ollama failure, logs and returns 0 —
    the rest of the indexing run continues with the remaining notes.
    """
    if not prepared:
        return 0
    inputs = [p[0] for p in prepared]
    try:
        embeddings = get_embeddings_batch(inputs, task="search_document")
    except Exception as e:
        logger.warning("Embedding batch failed (%d chunks): %s", len(inputs), e)
        return 0
    for (_, doc), embedding in zip(prepared, embeddings):
        doc["embedding"] = embedding
        batch_actions.append({
            "_index": OPENSEARCH_INDEX_NAME,
            "_id": f"{doc['document_id']}-{doc['chunk_index']}",
            "_source": doc,
        })
    return len(prepared)


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
    start_time = time.time()

    # Single delete_by_query covers all paths — cheaper than one-call-per-file.
    existing = [p for p in file_paths if (vault_root / p).exists()]
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
            total_chunks += _embed_and_extend(pending, batch_actions)
            pending = []
            if len(batch_actions) >= batch_size:
                _flush_batch(client, batch_actions)
                batch_actions.clear()

    if pending:
        total_chunks += _embed_and_extend(pending, batch_actions)
    if batch_actions:
        _flush_batch(client, batch_actions)

    client.indices.refresh(index=OPENSEARCH_INDEX_NAME)
    elapsed = time.time() - start_time

    return {
        "files_requested": len(file_paths),
        "notes_indexed": total_docs,
        "chunks_indexed": total_chunks,
        "chunks_deleted": deleted,
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
    """Bulk index a batch of actions."""
    try:
        success, errors = os_helpers.bulk(
            client, actions, refresh=False, raise_on_error=False
        )
        if errors:
            logger.warning("Bulk indexing had %d errors", len(errors))
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
