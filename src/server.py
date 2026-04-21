"""MCP server for hybrid search over Obsidian vault."""

import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .config import OBSIDIAN_VAULT_PATH
from .searcher import hybrid_search, keyword_search, list_notes as list_notes_search
from .indexer import index_vault, index_files, get_index_stats
from . import writer
from . import tagger

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
    resolved = tagger._resolve_path(file_path)
    if resolved is None:
        return f"Note not found: {file_path}"
    return resolved.read_text(encoding="utf-8")


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


# ============================================================================
# Todo tools
# ============================================================================

@mcp.tool()
def list_todos(
    tag: Optional[str] = None,
    status: str = "open",
    limit: int = 50,
) -> str:
    """List todos from TODO.md in the Obsidian vault.

    Args:
        tag: Filter by tag (e.g. "nasuni", "lucille", "inbox")
        status: "open", "completed", or "all" (default: "open")
        limit: Max todos to return (default 50)
    """
    return writer.list_todos(tag=tag, status=status, limit=limit)


@mcp.tool()
def add_todo(text: str, tags: Optional[str] = None) -> str:
    """Add a new todo to TODO.md.

    Inline hashtags in text are auto-detected (e.g. "#nasuni Fix the thing").
    The todo is placed under the matching section heading if one exists.

    Args:
        text: Todo text, optionally with inline #tags
        tags: Comma-separated tags to apply (e.g. "nasuni,review")
    """
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    return writer.add_todo(text=text, tags=tag_list)


@mcp.tool()
def complete_todo(todo_id: int) -> str:
    """Mark a todo as completed by its line ID.

    Use list_todos first to find the correct ID.

    Args:
        todo_id: Line number ID shown by list_todos
    """
    return writer.complete_todo(todo_id=todo_id)


@mcp.tool()
def search_todos(query: str) -> str:
    """Search todos by text content.

    Args:
        query: Text to search for within todo items
    """
    return writer.search_todos(query=query)


# ============================================================================
# Daily log tools
# ============================================================================

@mcp.tool()
def daily_log_view(date: Optional[str] = None) -> str:
    """Read a daily log note.

    Args:
        date: Date in YYYY-MM-DD format (default: today)
    """
    return writer.daily_log_view(date_str=date)


@mcp.tool()
def daily_log_create(date: Optional[str] = None, force: bool = False) -> str:
    """Create a daily log note with the standard template.

    Args:
        date: Date in YYYY-MM-DD format (default: today)
        force: Overwrite if log already exists (default false)
    """
    return writer.daily_log_create(date_str=date, force=force)


@mcp.tool()
def daily_log_append(content: str, section: Optional[str] = None, date: Optional[str] = None) -> str:
    """Append content to a daily log, optionally into a named section.

    Creates the log from the template first if it doesn't exist.

    Args:
        content: Text to append
        section: Section heading to insert under (e.g. "Notes 📝", "Tasks ✅", "Completed Today 🎉")
        date: Date in YYYY-MM-DD format (default: today)
    """
    return writer.daily_log_append(content=content, section=section, date_str=date)


@mcp.tool()
def daily_log_summary(days: int = 7) -> str:
    """Show which daily logs exist for the last N days.

    Args:
        days: Number of days to look back (default 7)
    """
    return writer.daily_log_summary(days=days)


# ============================================================================
# Note tools
# ============================================================================

@mcp.tool()
def note_create(
    title: str,
    content: str = "",
    folder: Optional[str] = None,
    tags: Optional[str] = None,
) -> str:
    """Create a new note in the Obsidian vault with YAML frontmatter.

    Args:
        title: Note title (also used as filename)
        content: Initial body content (markdown)
        folder: Vault-relative folder path (e.g. "KMW/Customers/Nasuni")
        tags: Comma-separated tags (e.g. "nasuni,opensearch")
    """
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    return writer.note_create(title=title, content=content, folder=folder, tags=tag_list)


@mcp.tool()
def note_append(file_path: str, content: str) -> str:
    """Append content to the end of an existing note.

    Args:
        file_path: Vault-relative path (e.g. "KMW/Customers/Nasuni/Meeting Notes.md")
        content: Markdown content to append
    """
    return writer.note_append(rel_path=file_path, content=content)


@mcp.tool()
def recent_notes(limit: int = 10) -> str:
    """List recently modified notes in the vault.

    Args:
        limit: Max notes to return (default 10)
    """
    return writer.recent_notes(limit=limit)


@mcp.tool()
def vault_stats() -> str:
    """Show vault statistics: note count, size, todo counts, top tags."""
    return writer.vault_stats()


# ============================================================================
# Bulk tag tools
# ============================================================================

@mcp.tool()
def bulk_tag_taxonomy() -> str:
    """Return the current tag vocabulary across the vault as JSON {tag: count},
    sorted by descending count. Use this as the seed taxonomy for bulk tag
    workflows.
    """
    return json.dumps(tagger.collect_taxonomy(), indent=2)


@mcp.tool()
def bulk_tag_taxonomy_topk(k: int = 100) -> str:
    """Return the top K tags by frequency as a newline-separated list.

    Optimized for subagent prompts (reduces token waste). Default k=100 covers
    ~80-90% of typical tagging needs while minimizing prompt overhead.
    """
    tags = tagger.collect_taxonomy_top_k(k)
    return "\n".join(tags)


@mcp.tool()
def bulk_tag_list() -> str:
    """List every .md note in the vault with path, size, and folder as JSON.
    Use this to enumerate notes for batched tag proposal workflows.
    """
    return json.dumps(tagger.list_notes(), indent=2)


@mcp.tool()
def bulk_tag_create_batches(paths: list[str], batch_size: Optional[int] = None) -> str:
    """Split a list of note paths into batch files for parallel agent processing.

    Creates batch_00.json, batch_01.json, etc. in logs/tag-run/batches/, clears
    stale files, and returns metadata. If batch_size is omitted, chooses adaptively
    based on vault size: <100 notes→20, 100-500→30, 500+→40. Use this as part of
    Step 2 in the workflow.
    """
    return json.dumps(tagger.create_batches(paths, batch_size), indent=2)


@mcp.tool()
def bulk_tag_apply(changes: list[dict]) -> str:
    """Apply a batch of tag merges to notes. Each change entry is
    {path: str, add_tags: list[str], remove_tags: list[str]}.

    Tags are normalized to lowercase kebab-case, deduplicated, aliased, and
    merged into existing frontmatter. Blocklisted tags are dropped; new
    proposals are capped at MAX_NEW_TAGS_PER_NOTE per note. Changes are
    always written to the vault.
    """
    return json.dumps(tagger.bulk_apply(changes), indent=2)


@mcp.tool()
def bulk_tag_prepare(paths: list[str]) -> str:
    """Prepare a batch of notes for tag proposal: returns per-note existing_tags
    and a head+tail content_excerpt. Replaces 20× read_note round trips per
    batch with one call and signals to agents which tags are already present.
    """
    return json.dumps(tagger.prepare_batch(paths), indent=2)


@mcp.tool()
def bulk_tag_verify(batch_file: str, result_file: str) -> str:
    """Verify a result file fully covers its batch input (and isn't stale).
    Returns {ok, missing_paths, extra_paths, stale, ...}. Use before apply.
    """
    return json.dumps(tagger.verify_batch(batch_file, result_file), indent=2)


@mcp.tool()
def bulk_tag_aggregate(results_dir: str) -> str:
    """Flatten all batch_*.json in results_dir, apply aliases, drop blocklisted
    tags and new-singletons. Returns {changes, rejected, consolidation_candidates}.
    """
    return json.dumps(tagger.aggregate_results(results_dir), indent=2)


@mcp.tool()
def bulk_tag_consolidate(
    changes: list[dict],
    consolidation_candidates: list[dict],
    confidence_threshold: float = 0.90,
) -> str:
    """Auto-merge near-duplicate tags in changes.

    For candidates with score >= confidence_threshold, automatically replace the
    proposed new tag with the existing nearest tag. Returns (updated_changes,
    flagged_for_review) where flagged_for_review are candidates with
    0.85 <= score < threshold.
    """
    updated, flagged = tagger.apply_consolidation(changes, consolidation_candidates, confidence_threshold)
    return json.dumps({
        "changes": updated,
        "flagged_for_review": flagged,
    }, indent=2)


@mcp.tool()
def bulk_tag_workflow() -> str:
    """Return the orchestration prompt for running the full bulk-tag workflow
    end-to-end: seed taxonomy, enumerate notes, dispatch Haiku subagents in
    parallel for per-note tag proposals, aggregate, apply, and report.

    Call this when the user wants to refresh tags across the whole vault.
    Follow the returned instructions step by step.
    """
    return tagger.workflow_prompt()


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
