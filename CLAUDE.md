# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A **FastMCP server for Obsidian vault search, write/management, and bulk tagging** — search notes by text and metadata, manage todos, write daily logs, and run bulk tag workflows. All operations read directly from vault `.md` files. No external services, no indexer, no embeddings.

The MCP server name is `obsidian-search` for backward compatibility.

## Commands

```bash
# Install dependencies
uv sync

# Run the MCP server (stdio)
uv run obsidian-mcp
# or: python -m src.server

# Shell CLI (same Python codepath as the MCP tools; used by cron/automation)
uv run obsidian-cli <subcommand>
# e.g. obsidian-cli search "nasuni" | list-notes --folder "Daily Log" | list-todos | daily-log view

# Tests
uv run pytest tests/
```

No Docker is required.

## Architecture

A single path: MCP tools / `obsidian-cli` → directly read/write vault `.md` files.

**Search path** (text search + frontmatter filtering):
- `src/writer.py:search_notes()` — scans all vault `.md` files, matches query against title + body,
  filters by tags/folder/date. Returns top-N results sorted by recency with content snippets.
- `src/writer.py:list_notes()` — same filters but metadata-only (no text match).

**Write path** (MCP tools / `obsidian-cli` → vault files):
- `src/writer.py` performs the vault mutations — notes, todos, daily logs, `vault_stats`,
  `recent_notes`. All paths are vault-relative; absolute or `~`-prefixed paths are rejected.
- `src/tagger.py` performs read + bulk-tag operations — taxonomy collection, per-note
  frontmatter tag merges (`bulk_apply` validates every path before any write and supports
  `dry_run`), batch prepare/verify/aggregate/consolidate, and the workflow prompt.

## MCP tools (server.py)

- **Search:** `search_notes`, `list_notes`
- **Notes:** `read_note`, `note_create`, `note_append`, `recent_notes`, `vault_stats`
- **Todos:** `list_todos`, `add_todo`, `complete_todo`, `search_todos`
  (`search_todos` is a text match over `TODO.md`; `search_notes` searches the full vault)
- **Daily logs:** `daily_log_view`, `daily_log_create`, `daily_log_append`, `daily_log_summary`
- **Bulk tag:** `bulk_tag_taxonomy`, `bulk_tag_taxonomy_topk`, `bulk_tag_list`,
  `bulk_tag_create_batches`, `bulk_tag_prepare`, `bulk_tag_apply`, `bulk_tag_verify`,
  `bulk_tag_aggregate`, `bulk_tag_consolidate`, `bulk_tag_workflow`

`bulk_tag_workflow()` returns an orchestration prompt; the flow dispatches `general-purpose`
subagents (`model: "haiku"`) to propose tags per note in parallel batches, then applies via
`bulk_tag_apply`. Classification LLM work uses Haiku for cost.

## Module responsibilities

| File | Purpose |
|---|---|
| `src/server.py` | FastMCP server `obsidian-search`; registers all search + write/management + bulk-tag tools; `main()` runs stdio |
| `src/writer.py` | Vault search (`search_notes`, `list_notes`) + writes: notes, todos, daily logs, stats. Vault-relative path enforcement |
| `src/tagger.py` | `read_note` + all bulk-tag ops (taxonomy, batches, apply, verify, aggregate, consolidate, workflow) |
| `src/vault_parser.py` | YAML frontmatter parsing, doc-type inference, section-aware chunking helpers |
| `src/cli.py` | `obsidian-cli` shell entrypoint over `writer`/`tagger` |
| `src/config.py` | All config from `.env` with defaults |

## Configuration (`.env`)

```
OBSIDIAN_VAULT_PATH=~/Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-vault
TAXONOMY_CACHE_TTL_SECONDS=60   # TTL for collect_taxonomy(); 0 = rescan every call
READ_NOTE_CACHE_SIZE=64         # LRU for read_note() keyed on (path, mtime_ns); 0 = disable
CHUNK_SIZE=1000                 # used by vault_parser for doc-type inference
CHUNK_OVERLAP=200
```

## Deployment

- `uv run obsidian-mcp` starts the MCP server (stdio transport). Your MCP client (Claude Code, etc.)
  normally spawns it automatically from the client config.
- No daemon or launchd service is required.

## History

Through v0.1.x this was a hybrid-search server: OpenSearch (768-dim HNSW kNN + BM25), Ollama
`nomic-embed-text` embeddings, a cross-encoder reranker, and recency decay. As of v0.2.0 all of
that was removed and retrieval delegated to Synology FileIndexing via the `synology-search` MCP.
As of v0.3.0 the NAS sync watcher and synology-search dependency were removed; search is now a
direct vault file scan. If a doc, comment, or import still describes the old stack, the code —
not the prose — is the source of truth.

## Expert Skills

Use `/opensearch-expert` for any OpenSearch Query DSL or relevancy work (if a search backend is
ever re-introduced).
