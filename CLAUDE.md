# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is (and is not)

A **FastMCP server for Obsidian vault write/management** — notes, todos, daily logs, and
bulk tag operations — plus a file **watcher that mirrors vault `.md` files to a mounted
Synology share** so they become searchable elsewhere.

**Search and retrieval are NOT handled here.** They are delegated to the **synology-search
MCP**, which queries Synology FileIndexing over the NAS copy of the vault. There is **no
OpenSearch, no Ollama, no embeddings, no reranker, and no local vector index** in this
project anymore — that stack was removed (see "History" below). The MCP server name is
`obsidian-search` for backward compatibility, but it exposes only write/management tools.

## Commands

```bash
# Install dependencies
uv sync

# Run the MCP server (stdio) — write/management + bulk-tag tools
uv run obsidian-mcp
# or: python -m src.server

# Run the vault watcher — mirrors .md changes to the NAS sync mount
uv run obsidian-watcher
# or: python -m src.watcher

# Shell CLI (same Python codepath as the MCP tools; used by cron/automation)
uv run obsidian-cli <subcommand>
# e.g. obsidian-cli list-todos | add-todo "#nasuni ..." | daily-log view | taxonomy

# Tests
uv run pytest tests/
```

No Docker is required to run anything in the current architecture.

## Architecture

Two independent paths, both operating directly on vault `.md` files. Nothing in this
process indexes, embeds, or searches.

**Write path** (MCP tools / `obsidian-cli` → vault files):
- `src/writer.py` performs the vault mutations — notes, todos, daily logs, `vault_stats`,
  `recent_notes`. All paths are vault-relative; absolute or `~`-prefixed paths are rejected.
- `src/tagger.py` performs read + bulk-tag operations — taxonomy collection, per-note
  frontmatter tag merges (`bulk_apply` validates every path before any write and supports
  `dry_run`), batch prepare/verify/aggregate/consolidate, and the workflow prompt.

**Sync path** (vault change → NAS → Synology index → synology-search):
1. `src/watcher.py` — watchdog observer over the vault, 10s debounce. Filters to `.md`
   files, excluding `.obsidian/` and `.trash/`. Tracks pending changes and deletes;
   handles modify/create/move/delete (a move enqueues the dest and deletes the source).
2. On each debounce flush it calls `src/nas_sync.py:sync_to_nas(changed, deleted)`, which
   `shutil.copyfile`s changed files to `NAS_VAULT_SYNC_PATH` and unlinks deleted ones.
   Uses `copyfile` (data only) because macOS SMB mounts reject xattr/metadata writes
   (`copy2` → `[Errno 22]`). Errors are logged and swallowed — sync must never disrupt
   the vault.
3. Synology FileIndexing picks up the mirrored files; retrieval happens through the
   **synology-search** MCP (lexical / BM25 over file contents and names). This is the
   single retrieval path for vault content today.

## MCP tools (server.py)

Write/management only — no search tools are registered.

- **Notes:** `read_note`, `note_create`, `note_append`, `recent_notes`, `vault_stats`
- **Todos:** `list_todos`, `add_todo`, `complete_todo`, `search_todos`
  (`search_todos` is a text match over `TODO.md`, not semantic search)
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
| `src/server.py` | FastMCP server `obsidian-search`; registers the write/management + bulk-tag tools; `main()` runs stdio |
| `src/writer.py` | Vault writes: notes, todos, daily logs, stats. Vault-relative path enforcement |
| `src/tagger.py` | `read_note` + all bulk-tag ops (taxonomy, batches, apply, verify, aggregate, consolidate, workflow) |
| `src/vault_parser.py` | YAML frontmatter parsing, doc-type inference, section-aware chunking helpers (still used for parsing; no longer feeds embeddings) |
| `src/watcher.py` | watchdog observer, 10s debounce, routes flushes to `nas_sync` |
| `src/nas_sync.py` | Mirrors changed/deleted `.md` files to the mounted Synology share |
| `src/cli.py` | `obsidian-cli` shell entrypoint over `writer`/`tagger` |
| `src/config.py` | All config from `.env` with defaults |

## Configuration (`.env`)

```
OBSIDIAN_VAULT_PATH=~/Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-vault
TAXONOMY_CACHE_TTL_SECONDS=60   # TTL for collect_taxonomy(); 0 = rescan every call
READ_NOTE_CACHE_SIZE=64         # LRU for read_note() keyed on (path, mtime_ns); 0 = disable
CHUNK_SIZE=1000                 # used by vault_parser for doc-type inference
CHUNK_OVERLAP=200
NAS_SYNC_ENABLED=false          # set true to enable NAS mirroring in the watcher
NAS_VAULT_SYNC_PATH=            # mounted share dir, e.g. /Volumes/Blanton/obsidian-vault
```

There are no OpenSearch / Ollama / embedding / reranker / recency settings — those were
removed with the search backend.

## Relationship to synology-search

The vault is searchable only because the watcher mirrors it to the NAS (default target
`/Volumes/Blanton/obsidian-vault`, indexed by Synology as `/Blanton/obsidian-vault`). The
`synology-search` MCP's `search_vault` tool is then the way to find vault content. That
search is **lexical only** — Synology's embedded Elasticsearch does not expose vector/kNN,
so there is currently no hybrid (dense + lexical) search anywhere in the setup.

## Deployment

- **macOS launchd daemon** `com.obsidian.search-watcher.plist` keeps `obsidian-watcher`
  running at startup; logs go to `logs/`. Its job now is NAS mirroring (not indexing).
- The watcher auto-mirrors `.md` changes with a 10s debounce; no manual step is needed.

## History

Through v0.1.x this was a hybrid-search server: OpenSearch (768-dim HNSW kNN + BM25 via a
hybrid pipeline with min-max normalization and an RRF fallback), Ollama `nomic-embed-text`
embeddings, a cross-encoder reranker, chunk-level embed caching, and recency decay. As of
v0.2.0 all of that was removed and retrieval was consolidated onto Synology FileIndexing
(via the synology-search MCP). If a doc, comment, or import still describes that stack, the
code — not the prose — is the source of truth.

## Expert Skills

Use `/opensearch-expert` for any OpenSearch Query DSL or relevancy work (relevant to the
synology-search side or any future re-introduction of a search backend).
