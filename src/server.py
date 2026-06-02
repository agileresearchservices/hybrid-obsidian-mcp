"""MCP server for Obsidian vault management (write/management tools only).

Search and retrieval is handled by the synology-search MCP, which indexes
vault files synced to the NAS by the vault watcher.
"""

import json
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import writer
from . import tagger

logger = logging.getLogger(__name__)

mcp = FastMCP("obsidian-search")


# ============================================================================
# Note tools
# ============================================================================

@mcp.tool()
def read_note(file_path: str) -> str:
    """Read the full content of a specific Obsidian note.

    Args:
        file_path: Path relative to vault root (e.g. "Daily Log/2026-04-07.md")
    """
    content = tagger.read_note(file_path)
    if content is None:
        return f"Note not found: {file_path}"
    return content


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
        section: Section heading to insert under (e.g. "Notes 📝", "Tasks ✅")
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

    Optimized for subagent prompts. Default k=100 covers ~80-90% of typical
    tagging needs while minimizing prompt overhead.
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
    stale files, and returns metadata.
    """
    return json.dumps(tagger.create_batches(paths, batch_size), indent=2)


@mcp.tool()
def bulk_tag_apply(changes: list[dict], dry_run: bool = False) -> str:
    """Apply a batch of tag merges to notes. Each change entry is
    {path: str, add_tags: list[str], remove_tags: list[str]}.

    All paths are validated up front — if any path is missing, the batch is
    aborted. Pass dry_run=True to preview without mutating the vault.
    """
    return json.dumps(tagger.bulk_apply(changes, dry_run=dry_run), indent=2)


@mcp.tool()
def bulk_tag_prepare(paths: list[str]) -> str:
    """Prepare a batch of notes for tag proposal: returns per-note existing_tags
    and a head+tail content_excerpt.
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

    Returns (updated_changes, flagged_for_review) where flagged items have
    0.85 <= score < threshold.
    """
    updated, flagged = tagger.apply_consolidation(changes, consolidation_candidates, confidence_threshold)
    return json.dumps({"changes": updated, "flagged_for_review": flagged}, indent=2)


@mcp.tool()
def bulk_tag_workflow() -> str:
    """Return the orchestration prompt for running the full bulk-tag workflow
    end-to-end. Call this when the user wants to refresh tags across the vault.
    """
    return tagger.workflow_prompt()


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
