"""MCP server for hybrid search over Obsidian vault."""

import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .config import OBSIDIAN_VAULT_PATH
from .searcher import hybrid_search, keyword_search, list_notes as list_notes_search
from .indexer import index_vault, index_files, get_index_stats

mcp = FastMCP("obsidian-search")


@mcp.tool()
def search_notes(
    query: str,
    k: int = 5,
    tags: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    folder: Optional[str] = None,
    rerank: bool = True,
) -> str:
    """Search Obsidian vault notes using hybrid search (semantic + lexical).

    Combines kNN vector similarity with BM25 full-text search, then reranks
    results using a cross-encoder model for optimal relevance.

    Args:
        query: Natural language search query
        k: Number of results to return (default 5)
        tags: Comma-separated tags to filter by (e.g. "nasuni,lucille")
        date_from: Filter notes from this date (YYYY-MM-DD)
        date_to: Filter notes up to this date (YYYY-MM-DD)
        folder: Filter by folder path (e.g. "Daily Log", "KMW/Customers")
        rerank: Whether to apply cross-encoder reranking (default true)
    """
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    results = hybrid_search(
        query=query,
        k=k,
        tags=tag_list,
        date_from=date_from,
        date_to=date_to,
        folder=folder,
        rerank=rerank,
    )

    if not results:
        return "No results found."

    output = []
    for i, r in enumerate(results, 1):
        title = r.metadata.get("title", "Unknown")
        date = r.metadata.get("date", "")
        file_path = r.metadata.get("file_path", "")
        score = r.score
        doc_type = r.metadata.get("doc_type", "")
        note_tags = r.metadata.get("tags", [])

        header = f"### {i}. {title}"
        if date:
            header += f" ({date})"
        header += f" [score: {score:.3f}]"

        meta_parts = []
        if file_path:
            meta_parts.append(f"File: {file_path}")
        if doc_type:
            meta_parts.append(f"Type: {doc_type}")
        if note_tags:
            meta_parts.append(f"Tags: {', '.join(note_tags) if isinstance(note_tags, list) else note_tags}")

        output.append(header)
        if meta_parts:
            output.append("  ".join(meta_parts))
        output.append("")
        output.append(r.chunk_text)
        output.append("")

    return "\n".join(output)


@mcp.tool()
def read_note(file_path: str) -> str:
    """Read the full content of a specific Obsidian note.

    Args:
        file_path: Path relative to vault root (e.g. "Daily Log/2026-04-07.md")
    """
    vault_root = Path(OBSIDIAN_VAULT_PATH)
    full_path = vault_root / file_path

    if not full_path.exists():
        return f"Note not found: {file_path}"

    if not full_path.is_relative_to(vault_root):
        return "Invalid path: must be within the vault."

    return full_path.read_text(encoding="utf-8")


@mcp.tool()
def list_notes(
    folder: Optional[str] = None,
    tags: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 20,
) -> str:
    """List Obsidian notes matching filters (without full-text search).

    Args:
        folder: Filter by folder (e.g. "Daily Log", "KMW")
        tags: Comma-separated tags to filter by
        date_from: Filter from date (YYYY-MM-DD)
        date_to: Filter to date (YYYY-MM-DD)
        limit: Max notes to return (default 20)
    """
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    results = list_notes_search(
        folder=folder,
        tags=tag_list,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )

    if not results:
        return "No notes found matching filters."

    output = []
    for note in results:
        title = note.get("title", "?")
        date = note.get("date", "")
        fp = note.get("file_path", "")
        note_tags = note.get("tags", [])

        line = f"- **{title}**"
        if date:
            line += f" ({date})"
        if fp:
            line += f" — `{fp}`"
        if note_tags:
            tags_str = ", ".join(note_tags) if isinstance(note_tags, list) else note_tags
            line += f" [{tags_str}]"
        output.append(line)

    return "\n".join(output)


@mcp.tool()
def index_notes(file_paths: list[str]) -> str:
    """Incrementally index specific notes into OpenSearch.

    Deletes existing chunks for each file and re-indexes with fresh embeddings.
    Use this after creating or updating notes to keep the search index current.

    Args:
        file_paths: List of paths relative to vault root (e.g. ["Daily Log/2026-04-08.md"])
    """
    stats = index_files(file_paths)
    return json.dumps(stats, indent=2)


@mcp.tool()
def reindex_vault() -> str:
    """Re-index the entire Obsidian vault into OpenSearch.

    This will delete all existing indexed data and re-crawl the vault,
    generating fresh embeddings and indexing all notes.
    """
    stats = index_vault()
    return json.dumps(stats, indent=2)


@mcp.tool()
def index_stats() -> str:
    """Show current index statistics - document counts, types, tags, etc."""
    stats = get_index_stats()
    return json.dumps(stats, indent=2)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
