"""Vault indexer - crawls Obsidian vault, generates embeddings via Ollama, bulk indexes to OpenSearch."""

import logging
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Optional

import httpx
from opensearchpy import helpers as os_helpers

from .config import (
    OLLAMA_BASE_URL,
    OLLAMA_EMBED_MODEL,
    OPENSEARCH_INDEX_NAME,
)
from .opensearch_client import create_client, ensure_index
from .vault_parser import parse_vault, ParsedNote

logger = logging.getLogger(__name__)


def get_embedding(text: str, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_EMBED_MODEL) -> list[float]:
    """Get embedding vector from Ollama."""
    response = httpx.post(
        f"{base_url}/api/embed",
        json={"model": model, "input": text},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"][0]


def make_doc_id(file_path: str) -> str:
    """Generate a stable document ID from file path."""
    return sha256(file_path.encode()).hexdigest()[:16]


def index_note(note: ParsedNote, client, batch_actions: list, vault_root: Optional[Path] = None) -> int:
    """Add a note's chunks to the batch action list. Returns chunk count."""
    doc_id = make_doc_id(note.file_path)
    chunk_count = 0

    for chunk_idx, chunk_text in enumerate(note.chunks):
        try:
            # Enriched embedding input: task prefix + metadata context for nomic-embed-text
            tags_str = ", ".join(note.tags[:8]) if note.tags else ""
            embed_input = (
                f"search_document: Title: {note.title}\n"
                f"Type: {note.doc_type}\n"
                f"Tags: {tags_str}\n\n"
                f"{chunk_text}"
            )
            embedding = get_embedding(embed_input)
        except Exception as e:
            logger.warning("Embedding failed for %s chunk %d: %s", note.file_path, chunk_idx, e)
            continue

        doc = {
            "document_id": doc_id,
            "chunk_index": chunk_idx,
            "chunk_text": chunk_text,
            "embedding": embedding,
            "title": note.title,
            "tags": note.tags,
            "folder": note.folder,
            "file_path": note.file_path,
            "doc_type": note.doc_type,
        }
        # Only include date if it's a valid YYYY-MM-DD
        if note.date and len(note.date) == 10 and note.date[4] == "-" and note.date[7] == "-":
            doc["date"] = note.date

        # Capture file modification time (mtime) as epoch milliseconds
        if vault_root:
            abs_path = vault_root / note.file_path
            if abs_path.exists():
                doc["file_mtime"] = int(abs_path.stat().st_mtime * 1000)

        batch_actions.append({
            "_index": OPENSEARCH_INDEX_NAME,
            "_id": f"{doc_id}-{chunk_idx}",
            "_source": doc,
        })
        chunk_count += 1

    return chunk_count


def index_files(file_paths: list[str], vault_path: Optional[str] = None, batch_size: int = 50) -> dict:
    """Incrementally index specific files into OpenSearch.

    Deletes existing chunks for each file, then re-parses and re-indexes.
    File paths should be relative to the vault root (e.g. "Daily Log/2026-04-08.md").

    Returns stats dict with counts.
    """
    from pathlib import Path
    from .config import OBSIDIAN_VAULT_PATH

    vault_root = Path(vault_path or OBSIDIAN_VAULT_PATH)
    client = create_client()
    ensure_index(client)

    total_docs = 0
    total_chunks = 0
    errors = 0
    deleted = 0
    batch_actions: list = []
    start_time = time.time()

    for rel_path in file_paths:
        full_path = vault_root / rel_path
        if not full_path.exists():
            logger.warning("File not found: %s", full_path)
            errors += 1
            continue

        doc_id = make_doc_id(rel_path)

        # Delete existing chunks for this document
        try:
            client.delete_by_query(
                index=OPENSEARCH_INDEX_NAME,
                body={"query": {"term": {"document_id": doc_id}}},
                refresh=True,
            )
            deleted += 1
        except Exception as e:
            logger.debug("No existing chunks to delete for %s: %s", rel_path, e)

        # Parse and re-index
        from .vault_parser import parse_note
        note = parse_note(full_path, vault_root)
        if not note:
            logger.warning("Could not parse: %s", rel_path)
            errors += 1
            continue

        try:
            chunks = index_note(note, client, batch_actions, vault_root=vault_root)
            if chunks > 0:
                total_docs += 1
                total_chunks += chunks
        except Exception as e:
            logger.warning("Error indexing %s: %s", rel_path, e)
            errors += 1

        if len(batch_actions) >= batch_size:
            _flush_batch(client, batch_actions)
            batch_actions.clear()

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
    start_time = time.time()

    from .config import OBSIDIAN_VAULT_PATH
    vault_root = Path(vault_path or OBSIDIAN_VAULT_PATH)

    for i, note in enumerate(notes, 1):
        try:
            chunks = index_note(note, client, batch_actions, vault_root=vault_root)
            if chunks > 0:
                total_docs += 1
                total_chunks += chunks
        except Exception as e:
            logger.warning("Error indexing %s: %s", note.file_path, e)
            errors += 1

        # Flush batch
        if len(batch_actions) >= batch_size:
            _flush_batch(client, batch_actions)
            batch_actions.clear()

        if i % 10 == 0:
            print(f"  Processed {i}/{len(notes)} notes ({total_chunks} chunks)...")

    # Final flush
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
