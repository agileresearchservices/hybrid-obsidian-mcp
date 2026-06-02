"""Smoke tests for the MCP server — tools list."""

import asyncio
from src import server


def _get_tool_names():
    tools = asyncio.run(server.mcp.list_tools())
    return {t.name for t in tools}


def test_mcp_has_expected_tools():
    names = _get_tool_names()
    for expected in (
        "read_note", "note_create", "note_append", "recent_notes", "vault_stats",
        "list_todos", "add_todo", "complete_todo", "search_todos",
        "daily_log_view", "daily_log_create", "daily_log_append", "daily_log_summary",
        "bulk_tag_workflow",
    ):
        assert expected in names, f"missing tool: {expected}"


def test_removed_tools_are_gone():
    names = _get_tool_names()
    for gone in ("search_notes", "index_notes", "reindex_vault", "index_stats", "cache_stats", "list_notes"):
        assert gone not in names, f"tool should be gone: {gone}"
